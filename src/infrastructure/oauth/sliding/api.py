"""The token endpoints for the sliding mode: refresh, revoke, and list tokens.

Mounted at ``/auth/token`` when DJANGO_AUTH_TOKEN_MODE is ``sliding``.
"""

from django.http import HttpRequest
from ninja import Router

from infrastructure.auth.core.sessions import api_auth, revoke_credentials
from infrastructure.common.errors import ApiError
from infrastructure.common.responses import ResponseTitle
from infrastructure.oauth.core.exchange import router as exchange_router
from infrastructure.oauth.core.schemas import (
    CredentialsOut,
    MessageOut,
    RefreshIn,
    RevokeIn,
    SessionListOut,
    SessionOut,
    credentials_out,
)
from infrastructure.oauth.sliding.services import (
    MODE,
    live_tokens,
    revoke_token,
    slide,
)

router = Router()
# Mode-independent: it only asks `issue_credentials` for whatever this mode
# issues, so all three publish it at the same path.
router.add_router("", exchange_router)


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
    return credentials_out(slide(request, payload.refresh_token))


@router.post(
    "/revoke",
    response=MessageOut,
    auth=None,
    summary="Revoke the presented credential",
)
def revoke(request: HttpRequest, payload: RevokeIn) -> MessageOut:
    """Retire a credential. A token already gone reads as success, not failure."""
    revoke_credentials(request, payload.token)
    return MessageOut(detail="Credential revoked.")


@router.get(
    "/sessions",
    response=SessionListOut,
    auth=api_auth,
    summary="List this account's live tokens",
)
def sessions(request: HttpRequest) -> SessionListOut:
    return SessionListOut(
        mode=MODE,
        sessions=[
            SessionOut(
                session_id=str(record.id),
                created_at=record.issued_at.isoformat(),
                last_used_at=record.last_used_at.isoformat() if record.last_used_at else "",
                expires_at=record.expires_at.isoformat(),
                ip_address=record.issued_ip or "",
                user_agent=record.user_agent,
                auth_method=str(record.metadata.get("auth_method", "")),
            )
            for record in live_tokens(request.user)
        ],
    )


@router.delete(
    "/sessions/{session_id}",
    response={200: MessageOut, 404: MessageOut},
    auth=api_auth,
    summary="Revoke one of this account's tokens",
)
def end_session(request: HttpRequest, session_id: str) -> MessageOut:
    """Revoke a named token. Scoped to the caller, so one account cannot end another's."""
    if not revoke_token(request.user, session_id):
        raise ApiError("No such session.", status=404, title=ResponseTitle.SESSION_NOT_FOUND)
    return MessageOut(detail="Session ended.")
