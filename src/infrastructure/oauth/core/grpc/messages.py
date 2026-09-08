"""The token messages every mode answers with.

Declared once. As with the REST schemas and the GraphQL types, the shapes are
shared so that switching ``DJANGO_AUTH_TOKEN_MODE`` changes what a deployment
stores, not what its clients have to parse.
"""

from typing import Any

from rest_framework import serializers

from infrastructure.oauth.core.services import SessionList

CREDENTIALS_RESPONSE = [
    {"name": "token_type", "type": "string"},
    {"name": "access_token", "type": "string"},
    {"name": "refresh_token", "type": "string"},
    {"name": "expires_in", "type": "int32"},
    {"name": "session_id", "type": "string"},
]
MESSAGE_RESPONSE = [{"name": "detail", "type": "string"}]
REFRESH_REQUEST = [{"name": "refresh_token", "type": "string"}]
REVOKE_REQUEST = [{"name": "token", "type": "string"}]
END_SESSION_REQUEST = [{"name": "session_id", "type": "string"}]


class Session(serializers.Serializer[dict[str, object]]):
    """One live credential, as it appears inside a ``SessionList`` message."""

    session_id = serializers.CharField()
    created_at = serializers.CharField()
    last_used_at = serializers.CharField()
    expires_at = serializers.CharField()
    ip_address = serializers.CharField()
    user_agent = serializers.CharField()
    auth_method = serializers.CharField()
    current = serializers.BooleanField()


SESSION_LIST_RESPONSE = [
    {"name": "mode", "type": "string"},
    {"name": "sessions", "cardinality": "repeated", "type": Session},
]


def credentials_message(credentials: Any, pb2: Any) -> Any:
    """Render an issued credential into an app's own generated message."""
    return pb2.Credentials(
        token_type=credentials.token_type,
        access_token=credentials.access_token,
        refresh_token=credentials.refresh_token,
        expires_in=credentials.expires_in or 0,
        session_id=credentials.session_id,
    )


def session_list_message(sessions: SessionList, pb2: Any) -> Any:
    """Render a mode's live credentials into its own generated message."""
    return pb2.SessionList(
        mode=sessions.mode,
        sessions=[
            pb2.Session(
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
