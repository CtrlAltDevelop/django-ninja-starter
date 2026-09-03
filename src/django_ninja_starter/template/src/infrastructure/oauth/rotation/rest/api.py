"""The token endpoints for the rotation mode: refresh, revoke, and list families.

Mounted at ``/auth/token`` when DJANGO_AUTH_TOKEN_MODE is ``rotation``. Only
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
from infrastructure.oauth.rotation.services import token_service

router = Router()
router.add_router("", shared_token_router(token_service, noun="session"))


@router.post(
    "/refresh",
    response={200: CredentialsOut, 400: MessageOut, 401: MessageOut},
    auth=None,
    summary="Exchange a refresh token for a new pair",
)
def refresh(request: HttpRequest, payload: RefreshIn) -> CredentialsOut:
    """Spend the presented refresh token and return its successor.

    Presenting one that has already been spent ends the whole family: see
    :mod:`infrastructure.oauth.rotation.services` for why that is the safe read.
    """
    return credentials_out(token_service.refresh(request, refresh_token=payload.refresh_token))
