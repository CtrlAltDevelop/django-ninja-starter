"""GraphQL types shared by every authentication method.

One login shape for every method, as in the REST document: a client that can
read the result of a password login can read the result of a magic link without
learning a second shape.

The two types this file *describes* are imported only for type checking. A
deployment can enable a social provider and no first-party method at all, which
installs the OAuth apps and not the authentication core -- and `oauth.core`'s
own GraphQL types are built on these. Importing `flows` here at module scope
would drag `auth_core`'s models into a project that never installed them.
"""

from typing import TYPE_CHECKING

import strawberry

if TYPE_CHECKING:
    from infrastructure.auth.core.flows import LoginResult
    from infrastructure.auth.core.sessions import IssuedCredentials


@strawberry.type
class CredentialsType:
    """The credential a finished login hands back."""

    token_type: str
    access_token: str
    refresh_token: str
    expires_in: int | None
    session_id: str

    @classmethod
    def from_issued(cls, credentials: "IssuedCredentials") -> "CredentialsType":
        return cls(
            token_type=credentials.token_type,
            access_token=credentials.access_token,
            refresh_token=credentials.refresh_token,
            expires_in=credentials.expires_in,
            session_id=credentials.session_id,
        )


@strawberry.type
class LoginType:
    """Either a credential or the ticket that stands in for one."""

    requires_second_factor: bool
    credentials: CredentialsType | None
    login_ticket: str
    methods: list[str]

    @classmethod
    def from_result(cls, result: "LoginResult") -> "LoginType":
        if result.credentials is not None:
            return cls(
                requires_second_factor=False,
                credentials=CredentialsType.from_issued(result.credentials),
                login_ticket="",
                methods=[],
            )
        return cls(
            requires_second_factor=True,
            credentials=None,
            login_ticket=result.pending_ticket,
            methods=result.methods,
        )


@strawberry.type
class ChallengeType:
    """Where a code went, and the handle for redeeming it."""

    ticket: str
    channel: str
    destination: str
    expires_in: int


@strawberry.type
class MessageType:
    """A plain acknowledgement."""

    detail: str
