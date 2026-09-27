"""Hosted payment gateways a customer can top their wallet up through.

The other half of :mod:`apps.wallet.hooks`. A signed webhook suits a processor
that calls us; the gateways here work the other way round, and so do almost all
of the Iranian ones: the customer is *sent* to the gateway, pays there, and is
sent back to us with a token in the URL. That return trip proves nothing -- the
customer's browser carried it, and the customer is the one party who must not be
able to credit themselves. So nothing on it is believed. What settles a deposit
is this server asking the gateway directly, with the merchant's own credential,
whether *that* token paid *that* amount. The answer to that call is the proof,
and it is the only one this module accepts.

Each gateway is configured the way a sign-in provider is: a block in
``settings.WALLET_GATEWAYS`` filled from the environment, and a gateway whose
required keys are empty is simply not offered::

    WALLET_GATEWAYS = {"zarinpal": {"merchant_id": "...", "sandbox": True}}

A gateway is reached through a configured :class:`~apps.wallet.catalog.PaymentMethod`
whose ``code`` is the gateway's key, so the fees, limits, currencies and approval
rules an operator sets in the admin apply to a gateway deposit exactly as they
do to any other.

Amounts: every Iranian gateway here takes whole **rials**, so their method must
be priced in ``IRR``; a wallet held in another currency converts at the method's
rate like any other deposit. Stripe and PayPal take the method's own currency.
"""

import base64
import hmac
import secrets
from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal
from typing import Any, ClassVar
from urllib.parse import urlencode
from xml.sax.saxutils import escape

import httpx
from django.conf import settings
from django.utils import timezone

from apps.wallet.errors import (
    CurrencyNotAllowed,
    GatewayUnavailable,
    InvalidAmount,
    MethodNotAllowed,
)


@dataclass(frozen=True)
class Payment:
    """One deposit, as a gateway needs to hear about it."""

    entry_id: str
    #: A number, because the bank gateways (Mellat, Sadad) take nothing else.
    order_id: int
    amount: Decimal
    currency: str
    callback_url: str
    description: str = ""


@dataclass(frozen=True)
class Redirect:
    """Where to send the customer. A ``POST`` means a form the client submits."""

    url: str
    method: str = "GET"
    fields: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class Started:
    #: The gateway's name for this payment. Checked against the return trip.
    authority: str
    redirect: Redirect


@dataclass(frozen=True)
class Verified:
    paid: bool
    reference: str = ""
    reason: str = ""


class Gateway:
    key: ClassVar[str] = ""
    label: ClassVar[str] = ""
    #: The config keys that have to be filled before the gateway is offered.
    required: ClassVar[tuple[str, ...]] = ()
    currencies: ClassVar[frozenset[str]] = frozenset({"IRR"})

    @property
    def config(self) -> dict[str, Any]:
        return dict((getattr(settings, "WALLET_GATEWAYS", {}) or {}).get(self.key, {}))

    @property
    def sandbox(self) -> bool:
        return bool(self.config.get("sandbox"))

    def is_configured(self) -> bool:
        return all(self.config.get(name) for name in self.required)

    def start(self, payment: Payment) -> Started:
        raise NotImplementedError

    def authority_from(self, params: dict[str, str]) -> str:
        """The token the return trip names. Believed only if it matches ours."""
        raise NotImplementedError

    def verify(self, payment: Payment, authority: str, params: dict[str, str]) -> Verified:
        """Ask the gateway, server to server, whether this payment went through."""
        raise NotImplementedError

    # -- shared plumbing --------------------------------------------------

    def rials(self, payment: Payment) -> int:
        if payment.currency != "IRR":
            raise CurrencyNotAllowed(f"{self.label} takes rials; price this method in IRR.")
        if payment.amount != payment.amount.to_integral_value():
            raise InvalidAmount(f"{self.label} takes whole rials.")
        return int(payment.amount)

    def call(
        self,
        method: str,
        url: str,
        *,
        json: Any = None,
        data: Any = None,
        content: str | None = None,
        headers: dict[str, str] | None = None,
        auth: Any = None,
    ) -> Any:
        """One request to the gateway. Anything short of readable JSON is its failure."""
        try:
            response = httpx.request(
                method,
                url,
                json=json,
                data=data,
                content=content,
                headers={"Accept": "application/json", **(headers or {})},
                auth=auth,
                timeout=float(getattr(settings, "WALLET_GATEWAY_TIMEOUT_SECONDS", 15)),
            )
            if content is not None:
                return response.text
            # Several gateways answer a refusal with a 4xx *and* a JSON body
            # saying why; the body is what we need, so the status is not raised.
            if response.status_code >= 500:
                response.raise_for_status()
            return response.json()
        except (httpx.HTTPError, ValueError) as error:
            raise GatewayUnavailable(f"{self.label} could not be reached.") from error

    @staticmethod
    def same(a: Any, b: Any) -> bool:
        return hmac.compare_digest(str(a or ""), str(b or ""))


def _refused(label: str, body: Any) -> GatewayUnavailable:
    return GatewayUnavailable(f"{label} refused to open the payment: {str(body)[:200]}")


# -- the aggregators --------------------------------------------------------


class Zarinpal(Gateway):
    key, label, required = "zarinpal", "Zarinpal", ("merchant_id",)

    @property
    def host(self) -> str:
        return "https://sandbox.zarinpal.com" if self.sandbox else "https://payment.zarinpal.com"

    def start(self, payment: Payment) -> Started:
        body = self.call(
            "POST",
            f"{self.host}/pg/v4/payment/request.json",
            json={
                "merchant_id": self.config["merchant_id"],
                "amount": self.rials(payment),
                "currency": "IRR",
                "callback_url": payment.callback_url,
                "description": payment.description or "Wallet top-up",
                "metadata": {"order_id": payment.entry_id},
            },
        )
        data = body.get("data") or {}
        if data.get("code") != 100 or not data.get("authority"):
            raise _refused(self.label, body.get("errors"))
        authority = str(data["authority"])
        return Started(authority, Redirect(f"{self.host}/pg/StartPay/{authority}"))

    def authority_from(self, params: dict[str, str]) -> str:
        return params.get("Authority", "")

    def verify(self, payment: Payment, authority: str, params: dict[str, str]) -> Verified:
        if params.get("Status") != "OK":
            return Verified(False, reason="Cancelled at the gateway.")
        body = self.call(
            "POST",
            f"{self.host}/pg/v4/payment/verify.json",
            json={
                "merchant_id": self.config["merchant_id"],
                "amount": self.rials(payment),
                "authority": authority,
            },
        )
        data = body.get("data") or {}
        # 101 is "verified already": a retried return trip, not a new payment.
        if data.get("code") in (100, 101):
            return Verified(True, reference=str(data.get("ref_id", "")))
        return Verified(False, reason=str(body.get("errors") or data)[:200])


class IDPay(Gateway):
    key, label, required = "idpay", "IDPay", ("api_key",)
    host = "https://api.idpay.ir/v1.1"

    @property
    def headers(self) -> dict[str, str]:
        return {"X-API-KEY": str(self.config["api_key"]), "X-SANDBOX": "1" if self.sandbox else "0"}

    def start(self, payment: Payment) -> Started:
        body = self.call(
            "POST",
            f"{self.host}/payment",
            headers=self.headers,
            json={
                "order_id": payment.entry_id,
                "amount": self.rials(payment),
                "callback": payment.callback_url,
                "desc": payment.description,
            },
        )
        if not body.get("id") or not body.get("link"):
            raise _refused(self.label, body)
        return Started(str(body["id"]), Redirect(str(body["link"])))

    def authority_from(self, params: dict[str, str]) -> str:
        return params.get("id", "")

    def verify(self, payment: Payment, authority: str, params: dict[str, str]) -> Verified:
        if params.get("status") not in ("10", "100", "101"):
            return Verified(False, reason=f"IDPay status {params.get('status')}.")
        body = self.call(
            "POST",
            f"{self.host}/payment/verify",
            headers=self.headers,
            json={"id": authority, "order_id": payment.entry_id},
        )
        if int(body.get("status") or 0) in (100, 101) and self.same(
            body.get("amount"), self.rials(payment)
        ):
            return Verified(True, reference=str(body.get("track_id", "")))
        return Verified(False, reason=str(body.get("error_message") or body)[:200])


class Zibal(Gateway):
    key, label = "zibal", "Zibal"
    host = "https://gateway.zibal.ir"

    def is_configured(self) -> bool:
        return self.sandbox or bool(self.config.get("merchant"))

    @property
    def merchant(self) -> str:
        return "zibal" if self.sandbox else str(self.config["merchant"])

    def start(self, payment: Payment) -> Started:
        body = self.call(
            "POST",
            f"{self.host}/v1/request",
            json={
                "merchant": self.merchant,
                "amount": self.rials(payment),
                "callbackUrl": payment.callback_url,
                "orderId": payment.entry_id,
                "description": payment.description,
            },
        )
        if body.get("result") != 100 or not body.get("trackId"):
            raise _refused(self.label, body.get("message"))
        track = str(body["trackId"])
        return Started(track, Redirect(f"{self.host}/start/{track}"))

    def authority_from(self, params: dict[str, str]) -> str:
        return params.get("trackId", "")

    def verify(self, payment: Payment, authority: str, params: dict[str, str]) -> Verified:
        if params.get("success") != "1":
            return Verified(False, reason="Cancelled at the gateway.")
        body = self.call(
            "POST", f"{self.host}/v1/verify", json={"merchant": self.merchant, "trackId": authority}
        )
        # 201 is "verified already".
        if body.get("result") in (100, 201) and self.same(body.get("amount"), self.rials(payment)):
            return Verified(True, reference=str(body.get("refNumber", "")))
        return Verified(False, reason=str(body.get("message") or body)[:200])


class NextPay(Gateway):
    key, label, required = "nextpay", "NextPay", ("api_key",)
    host = "https://nextpay.org/nx/gateway"

    def start(self, payment: Payment) -> Started:
        body = self.call(
            "POST",
            f"{self.host}/token",
            data={
                "api_key": self.config["api_key"],
                "order_id": payment.entry_id,
                "amount": self.rials(payment),
                "currency": "IRR",
                "callback_uri": payment.callback_url,
            },
        )
        if int(body.get("code", 0)) != -1 or not body.get("trans_id"):
            raise _refused(self.label, body)
        trans = str(body["trans_id"])
        return Started(trans, Redirect(f"{self.host}/payment/{trans}"))

    def authority_from(self, params: dict[str, str]) -> str:
        return params.get("trans_id", "")

    def verify(self, payment: Payment, authority: str, params: dict[str, str]) -> Verified:
        body = self.call(
            "POST",
            f"{self.host}/verify",
            data={
                "api_key": self.config["api_key"],
                "trans_id": authority,
                "amount": self.rials(payment),
                "currency": "IRR",
            },
        )
        if int(body.get("code", -99)) == 0:
            return Verified(True, reference=str(body.get("Shaparak_Ref_Id", "")))
        return Verified(False, reason=f"NextPay code {body.get('code')}.")


class _TokenGateway(Gateway):
    """Pay.ir and Vandar: one API shape, send a token out and verify it back."""

    required = ("api_key",)
    send_url = verify_url = pay_url = ""
    key_field = "api"

    def api_key(self) -> str:
        return str(self.config["api_key"])

    def start(self, payment: Payment) -> Started:
        body = self.call(
            "POST",
            self.send_url,
            json={
                self.key_field: self.api_key(),
                "amount": self.rials(payment),
                self.redirect_field: payment.callback_url,
                "factorNumber": payment.entry_id,
                "description": payment.description,
            },
        )
        if int(body.get("status") or 0) != 1 or not body.get("token"):
            raise _refused(self.label, body.get("errorMessage") or body.get("errors") or body)
        token = str(body["token"])
        return Started(token, Redirect(f"{self.pay_url}/{token}"))

    redirect_field = "redirect"

    def authority_from(self, params: dict[str, str]) -> str:
        return params.get("token", "")

    def verify(self, payment: Payment, authority: str, params: dict[str, str]) -> Verified:
        body = self.call(
            "POST", self.verify_url, json={self.key_field: self.api_key(), "token": authority}
        )
        if int(body.get("status") or 0) == 1 and self.same(body.get("amount"), self.rials(payment)):
            return Verified(True, reference=str(body.get("transId", "")))
        return Verified(False, reason=str(body.get("errorMessage") or body.get("errors"))[:200])


class PayIr(_TokenGateway):
    key, label = "payir", "Pay.ir"
    send_url, verify_url, pay_url = (
        "https://pay.ir/pg/send",
        "https://pay.ir/pg/verify",
        "https://pay.ir/pg",
    )

    def is_configured(self) -> bool:
        return self.sandbox or bool(self.config.get("api_key"))

    def api_key(self) -> str:
        return "test" if self.sandbox else str(self.config["api_key"])

    def verify(self, payment: Payment, authority: str, params: dict[str, str]) -> Verified:
        if params.get("status") != "1":
            return Verified(False, reason="Cancelled at the gateway.")
        return super().verify(payment, authority, params)


class Vandar(_TokenGateway):
    key, label = "vandar", "Vandar"
    key_field, redirect_field = "api_key", "callback_url"
    send_url, verify_url, pay_url = (
        "https://ipg.vandar.io/api/v3/send",
        "https://ipg.vandar.io/api/v3/verify",
        "https://ipg.vandar.io/v3",
    )

    def verify(self, payment: Payment, authority: str, params: dict[str, str]) -> Verified:
        if params.get("payment_status") != "OK":
            return Verified(False, reason="Cancelled at the gateway.")
        return super().verify(payment, authority, params)


# -- the banks' own gateways -------------------------------------------------


class Saman(Gateway):
    """SEP, Saman bank's gateway, over its token API."""

    key, label, required = "saman", "Saman (SEP)", ("terminal_id",)
    host = "https://sep.shaparak.ir"

    def start(self, payment: Payment) -> Started:
        body = self.call(
            "POST",
            f"{self.host}/onlinepg/onlinepg",
            json={
                "action": "token",
                "TerminalId": self.config["terminal_id"],
                "Amount": self.rials(payment),
                "ResNum": payment.entry_id,
                "RedirectUrl": payment.callback_url,
            },
        )
        if int(body.get("status") or 0) != 1 or not body.get("token"):
            raise _refused(self.label, body.get("errorDesc"))
        token = str(body["token"])
        return Started(
            token, Redirect(f"{self.host}/OnlinePG/SendToken?{urlencode({'token': token})}")
        )

    def authority_from(self, params: dict[str, str]) -> str:
        return params.get("Token", "")

    def verify(self, payment: Payment, authority: str, params: dict[str, str]) -> Verified:
        if params.get("State") != "OK" or not self.same(params.get("ResNum"), payment.entry_id):
            return Verified(False, reason=f"Saman state {params.get('State')}.")
        body = self.call(
            "POST",
            f"{self.host}/verifyTxnRandomSessionkey/ipg/VerifyTransaction",
            json={"RefNum": params.get("RefNum", ""), "TerminalNumber": self.config["terminal_id"]},
        )
        detail = body.get("TransactionDetail") or {}
        if body.get("Success") and self.same(detail.get("OrginalAmount"), self.rials(payment)):
            return Verified(True, reference=str(detail.get("RRN") or params.get("RefNum", "")))
        return Verified(False, reason=str(body.get("ResultDescription"))[:200])


class Mellat(Gateway):
    """Behpardakht Mellat. SOAP, so the envelope is written by hand."""

    key, label = "mellat", "Mellat (Behpardakht)"
    required = ("terminal_id", "username", "password")
    endpoint = "https://bpm.shaparak.ir/pgwchannel/services/pgw"
    start_url = "https://bpm.shaparak.ir/pgwchannel/startpay.mellat"

    def _soap(self, operation: str, **fields: Any) -> str:
        credentials = {
            "terminalId": self.config["terminal_id"],
            "userName": self.config["username"],
            "userPassword": self.config["password"],
        }
        inner = "".join(
            f"<{name}>{escape(str(value))}</{name}>"
            for name, value in {**credentials, **fields}.items()
        )
        envelope = (
            '<?xml version="1.0" encoding="utf-8"?>'
            '<soap:Envelope xmlns:soap="http://schemas.xmlsoap.org/soap/envelope/" '
            'xmlns:ns1="http://interfaces.core.sw.bps.com/">'
            f"<soap:Body><ns1:{operation}>{inner}</ns1:{operation}></soap:Body></soap:Envelope>"
        )
        text = self.call(
            "POST", self.endpoint, content=envelope, headers={"Content-Type": "text/xml"}
        )
        start, end = text.find("<return>"), text.find("</return>")
        if start < 0 or end < 0:
            raise GatewayUnavailable(f"{self.label} answered with something unreadable.")
        return str(text[start + len("<return>") : end]).strip()

    def start(self, payment: Payment) -> Started:
        now = timezone.localtime()
        answer = self._soap(
            "bpPayRequest",
            orderId=payment.order_id,
            amount=self.rials(payment),
            localDate=now.strftime("%Y%m%d"),
            localTime=now.strftime("%H%M%S"),
            additionalData=payment.entry_id,
            callBackUrl=payment.callback_url,
            payerId=0,
        )
        code, _, ref_id = answer.partition(",")
        if code != "0" or not ref_id:
            raise _refused(self.label, f"code {code}")
        return Started(ref_id, Redirect(self.start_url, "POST", {"RefId": ref_id}))

    def authority_from(self, params: dict[str, str]) -> str:
        return params.get("RefId", "")

    def verify(self, payment: Payment, authority: str, params: dict[str, str]) -> Verified:
        if params.get("ResCode") != "0" or not self.same(
            params.get("SaleOrderId"), payment.order_id
        ):
            return Verified(False, reason=f"Mellat code {params.get('ResCode')}.")
        sale = {
            "orderId": payment.order_id,
            "saleOrderId": payment.order_id,
            "saleReferenceId": params.get("SaleReferenceId", ""),
        }
        # 43 is "verified already". Settling is a second, separate call: an
        # unsettled Mellat payment is returned to the payer after a day.
        if self._soap("bpVerifyRequest", **sale) not in ("0", "43"):
            return Verified(False, reason="Mellat refused the verification.")
        if self._soap("bpSettleRequest", **sale) not in ("0", "45"):
            return Verified(False, reason="Mellat refused to settle.")
        return Verified(True, reference=str(params.get("SaleReferenceId", "")))


class Sadad(Gateway):
    """Sadad, Bank Melli's gateway. Every request is signed with Triple DES."""

    key, label = "sadad", "Sadad (Bank Melli)"
    required = ("merchant_id", "terminal_id", "terminal_key")
    host = "https://sadad.shaparak.ir"

    def _sign(self, text: str) -> str:
        from cryptography.hazmat.decrepit.ciphers.algorithms import TripleDES
        from cryptography.hazmat.primitives import padding
        from cryptography.hazmat.primitives.ciphers import Cipher, modes

        padder = padding.PKCS7(64).padder()
        block = padder.update(text.encode()) + padder.finalize()
        key = base64.b64decode(str(self.config["terminal_key"]))
        encryptor = Cipher(TripleDES(key), modes.ECB()).encryptor()  # noqa: S305 - Sadad's spec
        return base64.b64encode(encryptor.update(block) + encryptor.finalize()).decode()

    def start(self, payment: Payment) -> Started:
        terminal, amount = str(self.config["terminal_id"]), self.rials(payment)
        body = self.call(
            "POST",
            f"{self.host}/vpg/api/v0/Request/PaymentRequest",
            json={
                "MerchantId": self.config["merchant_id"],
                "TerminalId": terminal,
                "Amount": amount,
                "OrderId": payment.order_id,
                "LocalDateTime": timezone.localtime().strftime("%m/%d/%Y %I:%M:%S %p"),
                "ReturnUrl": payment.callback_url,
                "SignData": self._sign(f"{terminal};{payment.order_id};{amount}"),
            },
        )
        if str(body.get("ResCode")) != "0" or not body.get("Token"):
            raise _refused(self.label, body.get("Description"))
        token = str(body["Token"])
        return Started(token, Redirect(f"{self.host}/VPG/Purchase?{urlencode({'Token': token})}"))

    def authority_from(self, params: dict[str, str]) -> str:
        return params.get("token") or params.get("Token", "")

    def verify(self, payment: Payment, authority: str, params: dict[str, str]) -> Verified:
        if str(params.get("ResCode")) != "0":
            return Verified(False, reason=f"Sadad code {params.get('ResCode')}.")
        body = self.call(
            "POST",
            f"{self.host}/vpg/api/v0/Advice/Verify",
            json={"Token": authority, "SignData": self._sign(authority)},
        )
        if str(body.get("ResCode")) == "0" and self.same(body.get("Amount"), self.rials(payment)):
            return Verified(True, reference=str(body.get("RetrivalRefNo", "")))
        return Verified(False, reason=str(body.get("Description"))[:200])


# -- international -----------------------------------------------------------

#: Replaced by Stripe with the session id when it sends the customer back.
STRIPE_SESSION = "{CHECKOUT_SESSION_ID}"

#: Currencies Stripe counts in whole units rather than hundredths.
_ZERO_DECIMAL = frozenset({"JPY", "KRW", "VND", "CLP", "ISK", "UGX", "XAF", "XOF"})


def _cents(amount: Decimal, currency: str) -> int:
    scale = Decimal(1) if currency in _ZERO_DECIMAL else Decimal(100)
    return int((amount * scale).quantize(Decimal(1), rounding=ROUND_HALF_UP))


class Stripe(Gateway):
    """Stripe Checkout. The session is the authority; its status is the proof."""

    key, label, required = "stripe", "Stripe", ("secret_key",)
    currencies = frozenset()  # whatever the method is priced in
    host = "https://api.stripe.com/v1"

    @property
    def auth(self) -> tuple[str, str]:
        return (str(self.config["secret_key"]), "")

    def start(self, payment: Payment) -> Started:
        joiner = "&" if "?" in payment.callback_url else "?"
        body = self.call(
            "POST",
            f"{self.host}/checkout/sessions",
            auth=self.auth,
            data={
                "mode": "payment",
                "client_reference_id": payment.entry_id,
                "line_items[0][quantity]": 1,
                "line_items[0][price_data][currency]": payment.currency.lower(),
                "line_items[0][price_data][unit_amount]": _cents(payment.amount, payment.currency),
                "line_items[0][price_data][product_data][name]": payment.description
                or "Wallet top-up",
                # Stripe fills the placeholder in itself. Concatenated rather
                # than an f-string: doubled braces read as a template tag to
                # the project generator.
                "success_url": payment.callback_url + joiner + "session_id=" + STRIPE_SESSION,
                "cancel_url": f"{payment.callback_url}{joiner}cancelled=1",
            },
        )
        if not body.get("id") or not body.get("url"):
            raise _refused(self.label, (body.get("error") or {}).get("message"))
        return Started(str(body["id"]), Redirect(str(body["url"])))

    def authority_from(self, params: dict[str, str]) -> str:
        return params.get("session_id", "")

    def verify(self, payment: Payment, authority: str, params: dict[str, str]) -> Verified:
        if params.get("cancelled"):
            return Verified(False, reason="Cancelled at the gateway.")
        body = self.call("GET", f"{self.host}/checkout/sessions/{authority}", auth=self.auth)
        if (
            body.get("payment_status") == "paid"
            and self.same(body.get("client_reference_id"), payment.entry_id)
            and self.same(body.get("amount_total"), _cents(payment.amount, payment.currency))
            and str(body.get("currency", "")).upper() == payment.currency
        ):
            return Verified(True, reference=str(body.get("payment_intent") or authority))
        return Verified(False, reason=f"Stripe says {body.get('payment_status')}.")


class PayPal(Gateway):
    """PayPal Orders v2: create, send the payer to approve, capture on return."""

    key, label, required = "paypal", "PayPal", ("client_id", "client_secret")
    currencies = frozenset()

    @property
    def host(self) -> str:
        return "https://api-m.sandbox.paypal.com" if self.sandbox else "https://api-m.paypal.com"

    def _headers(self) -> dict[str, str]:
        body = self.call(
            "POST",
            f"{self.host}/v1/oauth2/token",
            data={"grant_type": "client_credentials"},
            auth=(str(self.config["client_id"]), str(self.config["client_secret"])),
        )
        if not body.get("access_token"):
            raise GatewayUnavailable("PayPal refused this deployment's credentials.")
        return {"Authorization": f"Bearer {body['access_token']}"}

    def start(self, payment: Payment) -> Started:
        joiner = "&" if "?" in payment.callback_url else "?"
        body = self.call(
            "POST",
            f"{self.host}/v2/checkout/orders",
            headers={**self._headers(), "PayPal-Request-Id": payment.entry_id},
            json={
                "intent": "CAPTURE",
                "purchase_units": [
                    {
                        "reference_id": payment.entry_id,
                        "amount": {
                            "currency_code": payment.currency,
                            "value": str(payment.amount.quantize(Decimal("0.01"))),
                        },
                    }
                ],
                "application_context": {
                    "return_url": payment.callback_url,
                    "cancel_url": f"{payment.callback_url}{joiner}cancelled=1",
                    "user_action": "PAY_NOW",
                },
            },
        )
        approve = next(
            (
                link["href"]
                for link in body.get("links", [])
                if link.get("rel") in ("approve", "payer-action")
            ),
            "",
        )
        if not body.get("id") or not approve:
            raise _refused(self.label, body.get("message"))
        return Started(str(body["id"]), Redirect(approve))

    def authority_from(self, params: dict[str, str]) -> str:
        return params.get("token", "")

    def verify(self, payment: Payment, authority: str, params: dict[str, str]) -> Verified:
        if params.get("cancelled"):
            return Verified(False, reason="Cancelled at the gateway.")
        body = self.call(
            "POST",
            f"{self.host}/v2/checkout/orders/{authority}/capture",
            headers={**self._headers(), "PayPal-Request-Id": f"capture-{payment.entry_id}"},
            json={},
        )
        units = body.get("purchase_units") or [{}]
        captures = ((units[0].get("payments") or {}).get("captures")) or [{}]
        amount = captures[0].get("amount") or {}
        if (
            body.get("status") == "COMPLETED"
            and amount.get("currency_code") == payment.currency
            and Decimal(str(amount.get("value") or "0")) == payment.amount.quantize(Decimal("0.01"))
        ):
            return Verified(True, reference=str(captures[0].get("id") or authority))
        return Verified(False, reason=f"PayPal says {body.get('status')}.")


GATEWAYS: dict[str, Gateway] = {
    gateway.key: gateway
    for gateway in (
        Zarinpal(),
        IDPay(),
        Zibal(),
        NextPay(),
        PayIr(),
        Vandar(),
        Saman(),
        Mellat(),
        Sadad(),
        Stripe(),
        PayPal(),
    )
}


def configured() -> dict[str, Gateway]:
    """The gateways this deployment has credentials for. The rest do not exist."""
    return {key: gateway for key, gateway in GATEWAYS.items() if gateway.is_configured()}


def order_number() -> int:
    """A positive number under 2**63 for the banks that only take a ``long``."""
    return secrets.randbelow(2**62) + 1


def return_base(method: str, derived: str = "") -> str:
    """Where a gateway sends the customer back to, less the entry id.

    ``WALLET_GATEWAY_CALLBACK_URL`` -- this wallet's public prefix -- when it is
    set, which it should be in production: ``derived`` comes from the request's
    Host header, and a spoofed one sends the customer back somewhere else.
    Nothing is settled by that trip either way. GraphQL and gRPC have no URL of
    their own to derive one from, so there the setting is required.
    """
    prefix = str(getattr(settings, "WALLET_GATEWAY_CALLBACK_URL", "") or derived)
    if not prefix:
        raise MethodNotAllowed(
            "Gateway deposits need DJANGO_WALLET_GATEWAY_CALLBACK_URL set to the wallet's "
            "public URL prefix."
        )
    return f"{prefix.rstrip('/')}/hooks/gateways/{method}"
