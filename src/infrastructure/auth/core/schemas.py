"""Response contracts every authentication method shares."""

from ninja import Schema

from infrastructure.auth.core.flows import LoginResult
from infrastructure.auth.core.sessions import IssuedCredentials


class MessageOut(Schema):
    """A plain acknowledgement or error explanation."""

    detail: str


class CredentialsOut(Schema):
    """The credential a finished login hands back."""

    token_type: str
    access_token: str = ""
    refresh_token: str = ""
    expires_in: int | None = None
    session_id: str = ""


class LoginOut(Schema):
    """Either a credential or the ticket that stands in for one.

    A single shape for both outcomes keeps clients from having to branch on the
    HTTP status to find out whether they are done.
    """

    requires_second_factor: bool
    credentials: CredentialsOut | None = None
    login_ticket: str = ""
    methods: list[str] = []


class ChallengeOut(Schema):
    """Where a code went, and the handle for redeeming it."""

    ticket: str
    channel: str
    destination: str
    expires_in: int


def mask_email(value: str) -> str:
    """Show enough of an address to recognise it, not enough to learn it."""
    local, separator, domain = value.partition("@")
    if not separator:
        return "***"
    return f"{local[:1]}***@{domain}"


def mask_phone(value: str) -> str:
    return f"***{value[-4:]}" if len(value) > 4 else "***"


def mask(channel: str, destination: str) -> str:
    if channel == "email":
        return mask_email(destination)
    if channel == "sms":
        return mask_phone(destination)
    return "***"


def credentials_out(credentials: IssuedCredentials) -> CredentialsOut:
    return CredentialsOut(
        token_type=credentials.token_type,
        access_token=credentials.access_token,
        refresh_token=credentials.refresh_token,
        expires_in=credentials.expires_in,
        session_id=credentials.session_id,
    )


def login_out(result: LoginResult) -> LoginOut:
    """Render a login outcome without leaking which branch the caller took."""
    if result.credentials is not None:
        return LoginOut(
            requires_second_factor=False,
            credentials=credentials_out(result.credentials),
        )
    return LoginOut(
        requires_second_factor=True,
        login_ticket=result.pending_ticket,
        methods=result.methods,
    )
