"""The token fields, built once and bound to whichever mode is active.

The three modes publish the same GraphQL surface for the same reason they
publish the same REST routes: a client should not have to know which mode is
behind its credentials. Each mode's ``graph/schema.py`` calls these builders
with its own service.
"""

from typing import Any

import strawberry
from strawberry.types import Info

from infrastructure.auth.core.graph.types import CredentialsType, MessageType
from infrastructure.common.graph.errors import require_caller, resolver
from infrastructure.common.identity import caller
from infrastructure.oauth.core.graph.types import SessionListType
from infrastructure.oauth.core.services import TokenModeService


def token_query(service: TokenModeService) -> type:
    """Build the read half: what credentials this account currently holds."""

    @strawberry.type
    class Query:
        @strawberry.field(description="The live credentials on the signed-in account.")
        @resolver
        def sessions(self, info: Info[Any, Any]) -> SessionListType:
            user = require_caller(caller(info.context.request))
            return SessionListType.from_list(service.sessions(user))

    return Query


def token_mutation(service: TokenModeService) -> type:
    """Build the write half: refresh, revoke, and end a named session."""

    @strawberry.type
    class Mutation:
        @strawberry.mutation(description="Renew the credential this deployment issues.")
        @resolver
        def refresh_token(self, info: Info[Any, Any], refresh_token: str = "") -> CredentialsType:
            return CredentialsType.from_issued(
                service.refresh(info.context.request, refresh_token=refresh_token)
            )

        @strawberry.mutation(description="Retire the presented credential.")
        @resolver
        def revoke_token(self, info: Info[Any, Any], token: str = "") -> MessageType:
            return MessageType(detail=service.revoke(info.context.request, token=token))

        @strawberry.mutation(description="End one of this account's sessions.")
        @resolver
        def end_session(self, info: Info[Any, Any], session_id: str) -> MessageType:
            user = require_caller(caller(info.context.request))
            return MessageType(detail=service.end_session(user, session_id))

        @strawberry.mutation(
            description="Exchange the browser session a social login left for a credential."
        )
        @resolver
        def exchange_session(self, info: Info[Any, Any]) -> CredentialsType:
            from infrastructure.oauth.core.services import session_exchange_service

            return CredentialsType.from_issued(
                session_exchange_service.exchange(info.context.request)
            )

    return Mutation
