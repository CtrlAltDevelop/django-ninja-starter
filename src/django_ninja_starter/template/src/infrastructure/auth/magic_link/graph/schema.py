"""Magic-link authentication, over GraphQL.

Asking for a link returns nothing that could redeem it -- the token exists only
in the message that was sent, which is the whole security property of this
method and is as true here as it is over HTTP.
"""

from typing import Any

import strawberry
from strawberry.types import Info

from infrastructure.auth.core.graph.types import LoginType, MessageType
from infrastructure.auth.magic_link.services import LinkSent, magic_link_service
from infrastructure.common.graph.errors import resolver


@strawberry.type
class LinkSentType:
    """What a caller learns after asking for a link: that one is on its way."""

    detail: str
    destination: str
    expires_in: int


def link_sent_type(sent: LinkSent) -> LinkSentType:
    return LinkSentType(
        detail=sent.detail, destination=sent.destination, expires_in=sent.expires_in
    )


@strawberry.type
class Mutation:
    @strawberry.mutation(description="Email a sign-up link.")
    @resolver
    def magic_link_signup_start(self, info: Info[Any, Any], email: str) -> LinkSentType:
        return link_sent_type(magic_link_service.start_signup(info.context.request, email=email))

    @strawberry.mutation(description="Email a sign-in link.")
    @resolver
    def magic_link_login_start(self, info: Info[Any, Any], email: str) -> LinkSentType:
        return link_sent_type(magic_link_service.start_login(info.context.request, email=email))

    @strawberry.mutation(description="Sign in with a link token.")
    @resolver
    def magic_link_verify(self, info: Info[Any, Any], token: str) -> LoginType:
        return LoginType.from_result(magic_link_service.verify(info.context.request, token=token))

    @strawberry.mutation(description="Sign out, retiring the presented credential.")
    @resolver
    def magic_link_logout(self, info: Info[Any, Any], token: str = "") -> MessageType:
        return MessageType(detail=magic_link_service.logout(info.context.request, token=token))
