"""The token endpoints for the sliding mode: refresh, revoke, and list tokens.

Mounted at ``/auth/token`` when DJANGO_AUTH_TOKEN_MODE is ``sliding``. Only
refresh is written here: what refreshing means is this mode's own, and the three
routes that read the same in every mode come from the shared builder.
"""

from django.http import HttpRequest
from ninja import Router

from infrastructure.oauth.core.rest.schemas import (
    CredentialsOut,
    MessageOut,
    RefreshIn,
    credentials_out,
)
from infrastructure.oauth.core.rest.tokens import shared_token_router
from infrastructure.oauth.sliding.services import token_service

router = Router()
router.add_router("", shared_token_router(token_service, noun="token"))


@router.post(
    "/refresh",
    response={200: CredentialsOut, 401: MessageOut},
    auth=None,
    summary="Push a sliding token's idle deadline out",
)
def refresh(request: HttpRequest, payload: RefreshIn) -> CredentialsOut:
    """Extend the idle window and report what is left.

    There is no separate refresh token in this mode, so the Authorization header
    is used when the body carries nothing.
    """
    return credentials_out(token_service.refresh(request, refresh_token=payload.refresh_token))
