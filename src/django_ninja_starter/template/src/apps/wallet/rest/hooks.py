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

from django.http import HttpRequest
from ninja import Router
from ninja.errors import HttpError

from apps.wallet import hooks
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
    """Settle or fail one movement, on the word of the rail that carried it.

    Send `X-Wallet-Timestamp` and `X-Wallet-Signature`, where the signature is
    the hex HMAC-SHA256 of `"{timestamp}.{raw body}"` under the secret configured
    for this method in `DJANGO_WALLET_WEBHOOK_SECRETS`. Anything that cannot be
    verified -- unsigned, wrongly signed, stale, or a method with no secret --
    gets one `401` with one sentence.

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
