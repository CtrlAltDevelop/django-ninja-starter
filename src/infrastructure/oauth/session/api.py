"""The token endpoints for the session mode: refresh, revoke, and list sessions.

Mounted at ``/auth/token`` when DJANGO_AUTH_TOKEN_MODE is ``session``.
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
from infrastructure.oauth.session.services import (
    MODE,
    live_sessions,
    refresh_access,
    revoke_session,
)

router = Router()
# Mode-independent: it only asks `issue_credentials` for whatever this mode
# issues, so all three publish it at the same path.
router.add_router("", exchange_router)


@router.post(
    "/refresh",
    response={200: CredentialsOut, 400: MessageOut, 401: MessageOut},
    auth=None,
    summary="Mint a new access token for a session",
)
def refresh(request: HttpRequest, payload: RefreshIn) -> CredentialsOut:
    """Trade the session key for a fresh access token. The key itself is unchanged."""
    if not payload.refresh_token:
        raise ApiError(
            "A refresh token is required.", status=400, title=ResponseTitle.TOKEN_REQUIRED
        )
    return credentials_out(refresh_access(request, payload.refresh_token))


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
    summary="List this account's live sessions",
)
def sessions(request: HttpRequest) -> SessionListOut:
    return SessionListOut(
        mode=MODE,
        sessions=[
            SessionOut(
                session_id=str(session.id),
                created_at=session.created_at.isoformat(),
                last_used_at=session.last_seen_at.isoformat(),
                expires_at=session.expires_at.isoformat(),
                ip_address=session.ip_address or "",
                user_agent=session.user_agent,
                auth_method=str(session.metadata.get("auth_method", "")),
            )
            for session in live_sessions(request.user)
        ],
    )


@router.delete(
    "/sessions/{session_id}",
    response={200: MessageOut, 404: MessageOut},
    auth=api_auth,
    summary="End one of this account's sessions",
)
def end_session(request: HttpRequest, session_id: str) -> MessageOut:
    """End a named session. Scoped to the caller, so one account cannot end another's."""
    if not revoke_session(request.user, session_id):
        raise ApiError("No such session.", status=404, title=ResponseTitle.SESSION_NOT_FOUND)
    return MessageOut(detail="Session ended.")
