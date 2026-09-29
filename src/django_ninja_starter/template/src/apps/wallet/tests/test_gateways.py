"""Topping a wallet up through a hosted gateway, with the gateway faked at the wire."""

import json
from decimal import Decimal
from typing import Any

import httpx
import pytest
from django.test import Client, override_settings

from apps.wallet import gateways
from apps.wallet.catalog import MethodCurrency, PaymentMethod
from apps.wallet.errors import GatewayUnavailable
from apps.wallet.methods import Method
from apps.wallet.models import EntryStatus
from apps.wallet.services import wallet_service
from apps.wallet.tests.conftest import bearer

pytestmark = pytest.mark.django_db

CONFIG = {
    "zarinpal": {"merchant_id": "m-1"},
    "zibal": {"merchant": "z-1"},
    "stripe": {"secret_key": "sk_test_1"},
}
BASE = "https://api.example.test/api/v1/wallet/hooks/gateways/zarinpal"


class Wire:
    """Answers each request with the next canned body, and remembers what was asked."""

    def __init__(self, *bodies: Any) -> None:
        self.bodies = list(bodies)
        self.sent: list[dict[str, Any]] = []

    def __call__(self, method: str, url: str, **kwargs: Any) -> httpx.Response:
        self.sent.append({"method": method, "url": url, **kwargs})
        body = self.bodies.pop(0)
        if isinstance(body, Exception):
            raise body
        return httpx.Response(200, json=body, request=httpx.Request(method, url))


@pytest.fixture(autouse=True)
def _configured() -> Any:
    with override_settings(WALLET_GATEWAYS=CONFIG, WALLET_CURRENCY="IRR"):
        yield


@pytest.fixture
def zarinpal(db: None) -> PaymentMethod:
    method = PaymentMethod.objects.create(
        code="zarinpal",
        name="Zarinpal",
        rail=str(Method.GATEWAY),
        is_enabled=True,
        supports_deposit=True,
        requires_approval=False,
    )
    MethodCurrency.objects.create(method=method, currency="IRR")
    return method


def _start(user: Any, monkeypatch: Any, amount: str = "50000") -> tuple[dict[str, Any], Wire]:
    wire = Wire({"data": {"code": 100, "authority": "A0001"}, "errors": []})
    monkeypatch.setattr(httpx, "request", wire)
    opened = wallet_service.start_gateway_deposit(
        user, method="zarinpal", amount=Decimal(amount), reference="top-1", callback_base=BASE
    )
    return opened, wire


def test_only_gateways_with_credentials_are_configured() -> None:
    assert set(gateways.configured()) == {"zarinpal", "zibal", "stripe"}


def test_starting_records_a_pending_deposit_and_a_redirect(
    alice: Any, zarinpal: PaymentMethod, monkeypatch: Any
) -> None:
    opened, wire = _start(alice, monkeypatch)

    assert opened["entry"]["status"] == str(EntryStatus.PENDING)
    assert opened["redirect"]["url"].endswith("/pg/StartPay/A0001")
    sent = wire.sent[0]["json"]
    assert sent["amount"] == 50000
    assert sent["callback_url"] == f"{BASE}/{opened['entry']['id']}"


def test_retrying_the_start_opens_no_second_payment(
    alice: Any, zarinpal: PaymentMethod, monkeypatch: Any
) -> None:
    first, wire = _start(alice, monkeypatch)
    again = wallet_service.start_gateway_deposit(
        alice, method="zarinpal", amount=Decimal("50000"), reference="top-1", callback_base=BASE
    )

    assert again["redirect"] == first["redirect"]
    assert len(wire.sent) == 1


def test_a_verified_payment_settles_the_deposit(
    alice: Any, zarinpal: PaymentMethod, monkeypatch: Any
) -> None:
    opened, _ = _start(alice, monkeypatch)
    wire = Wire({"data": {"code": 100, "ref_id": 777}})
    monkeypatch.setattr(httpx, "request", wire)

    done = wallet_service.complete_gateway_deposit(
        "zarinpal", opened["entry"]["id"], {"Authority": "A0001", "Status": "OK"}
    )

    assert done["status"] == str(EntryStatus.DONE)
    assert wire.sent[0]["json"] == {"merchant_id": "m-1", "amount": 50000, "authority": "A0001"}
    assert Decimal(str(wallet_service.balance(alice)["available"])) == Decimal("50000")


def test_a_return_trip_naming_another_token_changes_nothing(
    alice: Any, zarinpal: PaymentMethod, monkeypatch: Any
) -> None:
    opened, _ = _start(alice, monkeypatch)
    wire = Wire()
    monkeypatch.setattr(httpx, "request", wire)

    entry = wallet_service.complete_gateway_deposit(
        "zarinpal", opened["entry"]["id"], {"Authority": "FORGED", "Status": "OK"}
    )

    assert entry["status"] == str(EntryStatus.PENDING)
    assert wire.sent == []


def test_the_browser_saying_ok_is_not_believed(
    alice: Any, zarinpal: PaymentMethod, monkeypatch: Any
) -> None:
    opened, _ = _start(alice, monkeypatch)
    monkeypatch.setattr(httpx, "request", Wire({"data": {"code": -51}, "errors": ["unpaid"]}))

    entry = wallet_service.complete_gateway_deposit(
        "zarinpal", opened["entry"]["id"], {"Authority": "A0001", "Status": "OK"}
    )

    assert entry["status"] == str(EntryStatus.FAILED)
    assert Decimal(str(wallet_service.balance(alice)["available"])) == Decimal("0")


def test_an_unreachable_gateway_leaves_it_pending(
    alice: Any, zarinpal: PaymentMethod, monkeypatch: Any
) -> None:
    opened, _ = _start(alice, monkeypatch)
    monkeypatch.setattr(httpx, "request", Wire(httpx.ConnectError("down")))

    entry = wallet_service.complete_gateway_deposit(
        "zarinpal", opened["entry"]["id"], {"Authority": "A0001", "Status": "OK"}
    )

    assert entry["status"] == str(EntryStatus.PENDING)


def test_a_refused_start_fails_the_entry(
    alice: Any, zarinpal: PaymentMethod, monkeypatch: Any
) -> None:
    monkeypatch.setattr(httpx, "request", Wire({"data": {}, "errors": {"code": -9}}))

    with pytest.raises(GatewayUnavailable):
        wallet_service.start_gateway_deposit(
            alice, method="zarinpal", amount=Decimal("50000"), reference="r", callback_base=BASE
        )

    assert alice.wallet.entries.get().status == str(EntryStatus.FAILED)


def test_the_whole_trip_over_http(alice: Any, zarinpal: PaymentMethod, monkeypatch: Any) -> None:
    client = Client()
    monkeypatch.setattr(
        httpx, "request", Wire({"data": {"code": 100, "authority": "A0001"}, "errors": []})
    )
    started = client.post(
        "/api/v1/wallet/gateways/zarinpal/deposits",
        data=json.dumps({"amount": "50000", "reference": "http-1"}),
        content_type="application/json",
        **bearer(alice),
    )
    assert started.status_code == 200, started.content
    body = started.json()["data"]
    assert body["redirect"]["method"] == "GET"
    entry_id = body["entry"]["id"]

    monkeypatch.setattr(httpx, "request", Wire({"data": {"code": 100, "ref_id": 9}}))
    returned = client.get(
        f"/api/v1/wallet/hooks/gateways/zarinpal/{entry_id}?Authority=A0001&Status=OK"
    )

    assert returned.status_code == 200, returned.content
    assert returned.json()["data"]["status"] == str(EntryStatus.DONE)


def test_a_paid_deposit_that_needs_approval_waits_and_says_it_was_paid(
    alice: Any, zarinpal: PaymentMethod, monkeypatch: Any
) -> None:
    PaymentMethod.objects.filter(pk=zarinpal.pk).update(requires_approval=True)
    opened, _ = _start(alice, monkeypatch)
    monkeypatch.setattr(httpx, "request", Wire({"data": {"code": 100, "ref_id": 5}}))

    entry = wallet_service.complete_gateway_deposit(
        "zarinpal", opened["entry"]["id"], {"Authority": "A0001", "Status": "OK"}
    )

    assert entry["status"] == str(EntryStatus.PENDING)
    assert alice.wallet.entries.get().metadata["gateway_paid"] == "5"


def test_the_listing_offers_only_gateways_with_a_method(
    alice: Any, zarinpal: PaymentMethod
) -> None:
    assert wallet_service.offered_gateways() == [{"code": "zarinpal", "label": "Zarinpal"}]


# -- each gateway reads its own answers ------------------------------------

PAYMENT = gateways.Payment(
    entry_id="e-1",
    order_id=42,
    amount=Decimal("10000"),
    currency="IRR",
    callback_url="https://x.test/back",
)


def test_zibal_checks_the_amount_it_verified(monkeypatch: Any) -> None:
    gateway = gateways.GATEWAYS["zibal"]
    monkeypatch.setattr(httpx, "request", Wire({"result": 100, "amount": 1, "refNumber": "r"}))

    assert not gateway.verify(PAYMENT, "t", {"success": "1"}).paid


def test_stripe_insists_on_paid_and_the_same_entry(monkeypatch: Any) -> None:
    gateway = gateways.GATEWAYS["stripe"]
    usd = gateways.Payment("e-1", 1, Decimal("12.50"), "USD", "https://x.test/back")
    session = {
        "payment_status": "paid",
        "client_reference_id": "e-1",
        "amount_total": 1250,
        "currency": "usd",
        "payment_intent": "pi_1",
    }
    monkeypatch.setattr(httpx, "request", Wire(session, {**session, "client_reference_id": "x"}))

    assert gateway.verify(usd, "cs_1", {"session_id": "cs_1"}) == gateways.Verified(True, "pi_1")
    assert not gateway.verify(usd, "cs_1", {"session_id": "cs_1"}).paid


def test_iranian_gateways_refuse_anything_but_whole_rials() -> None:
    from apps.wallet.errors import CurrencyNotAllowed, InvalidAmount

    gateway = gateways.GATEWAYS["zarinpal"]
    with pytest.raises(CurrencyNotAllowed):
        gateway.rials(gateways.Payment("e", 1, Decimal("1"), "USD", ""))
    with pytest.raises(InvalidAmount):
        gateway.rials(gateways.Payment("e", 1, Decimal("1.5"), "IRR", ""))


def test_sadad_signs_with_triple_des() -> None:
    import base64

    with override_settings(
        WALLET_GATEWAYS={"sadad": {"terminal_key": base64.b64encode(b"k" * 24).decode()}}
    ):
        signed = gateways.Sadad()._sign("T;42;10000")

    assert len(base64.b64decode(signed)) == 16
