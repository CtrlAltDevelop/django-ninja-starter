"""The signed door a payment rail confirms movements through.

The one endpoint in this app that is not authenticated as an account, because
the caller is not one: a processor confirming that money moved has no user to be.
It proves itself with a signature over the body and a timestamp instead -- see
:mod:`apps.wallet.hooks` for the scheme and why each part of it is there.

This exists because the alternative is worse. Settling used to be published to
the account itself, on the reasoning that a deployment would put its webhook
behind that endpoint; what it actually meant was that any customer could confirm
their own deposit and credit themselves. There is no version of "the account says
the money arrived" that is safe, so the verb moved here, where saying it costs a
secret.
"""

from typing import Any
from urllib.parse import urlencode
from uuid import UUID

from django.conf import settings
from django.http import HttpRequest, HttpResponseRedirect
from ninja import Router
from ninja.errors import HttpError

from apps.wallet import gateways, hooks
from apps.wallet.errors import WalletError
from apps.wallet.rest.schemas import EntryOut, RailEventIn
from apps.wallet.services import wallet_service

#: No `auth`: the signature is the authentication, and an account credential
#: would mean nothing here anyway -- the rail does not have one.
router = Router(auth=None)


@router.post(
    "/{method_code}",
    response=EntryOut,
    summary="A payment rail confirming one movement",
    auth=None,
)
def rail_event(request: HttpRequest, method_code: str, payload: RailEventIn) -> dict[str, Any]:
    """Settle, fail or reverse one movement, on the word of the rail that carried it.

    Send `X-Wallet-Timestamp` and `X-Wallet-Signature`, where the signature is
    the hex HMAC-SHA256 of `"{timestamp}.{raw body}"` under the secret configured
    for this method in `DJANGO_WALLET_WEBHOOK_SECRETS`. Anything that cannot be
    verified -- unsigned, wrongly signed, stale, or a method with no secret --
    gets one `401` with one sentence.

    `reversed` is a chargeback: it writes the opposing entry and answers with
    it, and is refused for a rail whose payments are final -- a bank transfer, a
    chain confirmation.

    Idempotent: a rail that delivers the same confirmation twice gets the same
    entry back, because rails do exactly that.

    A movement waiting on an operator stays waiting. The rail's confirmation and
    the operator's approval are separate facts and this endpoint can only supply
    the first of them.
    """
    try:
        # Verified before anything is read or written, so an unsigned caller
        # cannot even learn whether an entry id exists.
        hooks.verify(
            method_code,
            request.body,
            request.headers.get(hooks.TIMESTAMP_HEADER, ""),
            request.headers.get(hooks.SIGNATURE_HEADER, ""),
        )
        return wallet_service.confirm_from_rail(
            payload.entry_id,
            method_code=method_code,
            event=payload.event,
            external_reference=payload.external_reference,
            reason=payload.reason,
        )
    except WalletError as refusal:
        raise HttpError(refusal.status, str(refusal)) from None


def gateway_return_base(request: HttpRequest, method: str) -> str:
    """The return address, derived from this request when no setting names one."""
    return gateways.return_base(
        method, request.build_absolute_uri(request.path.rsplit("/gateways/", 1)[0])
    )


@router.api_operation(
    ["GET", "POST"],
    "/gateways/{method}/{entry_id}",
    summary="The customer, back from a hosted gateway",
    auth=None,
    include_in_schema=False,
)
def gateway_return(request: HttpRequest, method: str, entry_id: UUID) -> Any:
    """Verify the payment with the gateway, then send the customer on.

    Gateways return by GET or by a form POST, so both are read. Nothing sent
    here is trusted: the service asks the gateway directly.
    """
    params = {**request.GET.dict(), **request.POST.dict()}
    try:
        entry = wallet_service.complete_gateway_deposit(method, entry_id, params)
    except WalletError as refusal:
        raise HttpError(refusal.status, str(refusal)) from None
    landing = getattr(settings, "WALLET_GATEWAY_RETURN_URL", "")
    if not landing:
        return entry
    joiner = "&" if "?" in landing else "?"
    query = urlencode({"entry": str(entry["id"]), "status": entry["status"]})
    return HttpResponseRedirect(f"{landing}{joiner}{query}")
