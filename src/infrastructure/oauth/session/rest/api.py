"""The token endpoints for the session mode: refresh, revoke, and list sessions.

Mounted at ``/auth/token`` when DJANGO_AUTH_TOKEN_MODE is ``session``. Only
refresh is written here; the three routes that read the same in every mode come
from the shared builder.
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
from infrastructure.oauth.session.services import token_service

router = Router()
router.add_router("", shared_token_router(token_service, noun="session"))


@router.post(
    "/refresh",
    response={200: CredentialsOut, 400: MessageOut, 401: MessageOut},
    auth=None,
    summary="Mint a new access token for a session",
)
def refresh(request: HttpRequest, payload: RefreshIn) -> CredentialsOut:
    """Trade the session key for a fresh access token. The key itself is unchanged."""
    return credentials_out(token_service.refresh(request, refresh_token=payload.refresh_token))
