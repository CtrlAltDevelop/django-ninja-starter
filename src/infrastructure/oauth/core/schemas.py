"""Request and response bodies the three token modes answer with.

The shapes are shared so that switching DJANGO_AUTH_TOKEN_MODE changes what a
deployment stores, not what its clients have to parse.
"""

from ninja import Schema

from infrastructure.oauth.core.credentials import IssuedCredentials


class MessageOut(Schema):
    """A plain acknowledgement or error explanation."""

    detail: str


class CredentialsOut(Schema):
    """A freshly minted credential, in the same shape a login returns."""

    token_type: str
    access_token: str = ""
    refresh_token: str = ""
    expires_in: int | None = None
    session_id: str = ""


class RefreshIn(Schema):
    """The refresh half of a pair. Empty for sliding, which has only one token."""

    refresh_token: str = ""


class RevokeIn(Schema):
    """Either half of a pair, or nothing at all to use the Authorization header."""

    token: str = ""


class SessionOut(Schema):
    """One live credential, as an account's own device list would show it."""

    session_id: str
    created_at: str
    last_used_at: str = ""
    expires_at: str
    ip_address: str = ""
    user_agent: str = ""
    auth_method: str = ""
    current: bool = False


class SessionListOut(Schema):
    mode: str
    sessions: list[SessionOut]


def credentials_out(credentials: IssuedCredentials) -> CredentialsOut:
    return CredentialsOut(
        token_type=credentials.token_type,
        access_token=credentials.access_token,
        refresh_token=credentials.refresh_token,
        expires_in=credentials.expires_in,
        session_id=credentials.session_id,
    )
