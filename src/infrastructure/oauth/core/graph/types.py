"""GraphQL types shared by the three token modes.

As with the REST schemas: the shapes are shared so that switching
``DJANGO_AUTH_TOKEN_MODE`` changes what a deployment stores, not what its
clients have to parse.
"""

import strawberry

from infrastructure.oauth.core.services import SessionList


@strawberry.type
class SessionType:
    """One live credential, as an account's own device list would show it."""

    session_id: str
    created_at: str
    last_used_at: str
    expires_at: str
    ip_address: str
    user_agent: str
    auth_method: str
    current: bool


@strawberry.type
class SessionListType:
    mode: str
    sessions: list[SessionType]

    @classmethod
    def from_list(cls, sessions: SessionList) -> "SessionListType":
        return cls(
            mode=sessions.mode,
            sessions=[
                SessionType(
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
