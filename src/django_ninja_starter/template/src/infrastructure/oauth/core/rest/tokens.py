"""The token routes that are identical whichever mode is active.

Revoking, listing and ending a session read the same in all three modes -- they
only ask the mode's own service. Writing them once is what makes "a client does
not have to know which mode it is talking to" true of the code and not just of
the URL.

Refreshing is not here: what a refresh *is* differs by mode, down to whether a
token has to be supplied at all, so each mode publishes its own.
"""

from django.http import HttpRequest
from ninja import Router

from infrastructure.oauth.core.rest.exchange import router as exchange_router
from infrastructure.oauth.core.rest.schemas import (
    MessageOut,
    RevokeIn,
    SessionListOut,
    SessionOut,
)
from infrastructure.oauth.core.services import SessionList, TokenModeService


def session_list_out(sessions: SessionList) -> SessionListOut:
    return SessionListOut(
        mode=sessions.mode,
        sessions=[
            SessionOut(
                session_id=session.session_id,
                created_at=session.created_at,
                last_used_at=session.last_used_at,
                expires_at=session.expires_at,
                ip_address=session.ip_address,
                user_agent=session.user_agent,
                auth_method=session.auth_method,
                current=session.current,
            )
            for session in sessions.sessions
        ],
    )


def shared_token_router(service: TokenModeService, *, noun: str) -> Router:
    """Build the routes every mode publishes, bound to one mode's service.

    ``noun`` is what this mode calls the thing being listed -- a sliding
    deployment has tokens where the other two have sessions -- so the summaries
    read correctly in each mode's own document.
    """
    from infrastructure.auth.core.sessions import api_auth

    router = Router()
    # Mode-independent: it only asks `issue_credentials` for whatever this mode
    # issues, so all three publish it at the same path.
    router.add_router("", exchange_router)

    @router.post(
        "/revoke",
        response=MessageOut,
        auth=None,
        summary="Revoke the presented credential",
    )
    def revoke(request: HttpRequest, payload: RevokeIn) -> MessageOut:
        """Retire a credential. A token already gone reads as success, not failure."""
        return MessageOut(detail=service.revoke(request, token=payload.token))

    @router.get(
        "/sessions",
        response=SessionListOut,
        auth=api_auth,
        summary=f"List this account's live {noun}s",
    )
    def sessions(request: HttpRequest) -> SessionListOut:
        return session_list_out(service.sessions(request.user))

    @router.delete(
        "/sessions/{session_id}",
        response={200: MessageOut, 404: MessageOut},
        auth=api_auth,
        summary=f"End one of this account's {noun}s",
    )
    def end_session(request: HttpRequest, session_id: str) -> MessageOut:
        """End a named session. Scoped to the caller, so one account cannot end another's."""
        return MessageOut(detail=service.end_session(request.user, session_id))

    return router
