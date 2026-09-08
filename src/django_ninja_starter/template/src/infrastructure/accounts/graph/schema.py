"""The accounts app's contribution to the project's GraphQL schema."""

from typing import Any

import strawberry
from strawberry.types import Info

from infrastructure.accounts.graph.types import AccountType, ProfileInput
from infrastructure.accounts.services import account_service
from infrastructure.common.graph.errors import require_caller, resolver
from infrastructure.common.identity import caller


def _caller(info: Info[Any, Any]) -> Any:
    """The account behind the credential on this request, or a refusal."""
    return require_caller(caller(info.context.request))


@strawberry.type
class Query:
    @strawberry.field(description="The signed-in account and its profile.")
    @resolver
    def me(self, info: Info[Any, Any]) -> AccountType:
        return AccountType.from_view(account_service.view(_caller(info)))


@strawberry.type
class Mutation:
    @strawberry.mutation(description="Update the signed-in account's profile.")
    @resolver
    def update_profile(self, info: Info[Any, Any], profile: ProfileInput) -> AccountType:
        account = account_service.update_profile(_caller(info), profile.changes())
        return AccountType.from_view(account)
