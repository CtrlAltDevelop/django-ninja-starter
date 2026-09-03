"""Password authentication, over GraphQL.

Every one of these changes something -- an account, a password, a credential --
so all of them are mutations. There is nothing to query: "am I signed in" is
`me` on the accounts app, and this app has no state of its own to read.

The service is the same one the routers call, so the rules are the same ones:
the timing defence on a wrong password, the reset code that answers identically
for an address with no account, and the 400-not-401 on a mistyped current
password (which arrives here as `status: 400` in the error's extensions).
"""

from typing import Any

import strawberry
from strawberry.types import Info

from infrastructure.auth.core.graph.types import LoginType, MessageType
from infrastructure.auth.password.services import password_service
from infrastructure.common.graph.errors import require_caller, resolver
from infrastructure.common.identity import caller


@strawberry.type
class ResetTicketType:
    """The answer to "send me a reset code", identical for unknown addresses."""

    detail: str
    ticket: str
    expires_in: int


@strawberry.type
class Mutation:
    @strawberry.mutation(description="Create an account with a password.")
    @resolver
    def password_signup(
        self, info: Info[Any, Any], identifier: str, password: str, email: str = ""
    ) -> LoginType:
        return LoginType.from_result(
            password_service.signup(
                info.context.request, identifier=identifier, password=password, email=email
            )
        )

    @strawberry.mutation(description="Sign in with a password.")
    @resolver
    def password_login(self, info: Info[Any, Any], identifier: str, password: str) -> LoginType:
        return LoginType.from_result(
            password_service.login(info.context.request, identifier=identifier, password=password)
        )

    @strawberry.mutation(description="Sign out, retiring the presented credential.")
    @resolver
    def password_logout(self, info: Info[Any, Any], token: str = "") -> MessageType:
        return MessageType(detail=password_service.logout(info.context.request, token=token))

    @strawberry.mutation(description="Request a password reset code.")
    @resolver
    def password_forgot(self, info: Info[Any, Any], email: str) -> ResetTicketType:
        ticket = password_service.forgot(info.context.request, email=email)
        return ResetTicketType(
            detail=ticket.detail, ticket=ticket.ticket, expires_in=ticket.expires_in
        )

    @strawberry.mutation(description="Set a new password with a reset code.")
    @resolver
    def password_reset(
        self, info: Info[Any, Any], ticket: str, code: str, password: str
    ) -> MessageType:
        detail = password_service.reset(
            info.context.request, ticket=ticket, code=code, password=password
        )
        return MessageType(detail=detail)

    @strawberry.mutation(description="Change the password on the signed-in account.")
    @resolver
    def password_change(
        self, info: Info[Any, Any], current_password: str, new_password: str
    ) -> MessageType:
        request = info.context.request
        detail = password_service.change(
            request,
            require_caller(caller(request)),
            current_password=current_password,
            new_password=new_password,
        )
        return MessageType(detail=detail)
