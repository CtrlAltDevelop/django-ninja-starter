"""What the signed-in account can read and change about itself, over gRPC.

The credential travels in the call's ``authorization`` metadata, which is where
:func:`grpc_caller` looks for it -- the same bearer token the REST and GraphQL
doors accept, checked by the same resolver.

A partial update needs a way to say "leave this alone", and proto3 says it with
``optional``: a field the caller did not set is absent rather than empty, so
clearing a field (empty string) stays distinct from not mentioning it.
"""

from typing import Any

from asgiref.sync import sync_to_async
from django_socio_grpc import generics
from django_socio_grpc.decorators import grpc_action

from infrastructure.accounts.grpc.serializers import Profile
from infrastructure.accounts.services import AccountView, account_service
from infrastructure.common.grpc.errors import action, require_caller
from infrastructure.common.identity import grpc_caller

PROFILE_FIELDS = [
    {"name": "display_name", "type": "string"},
    {"name": "avatar_url", "type": "string"},
    {"name": "bio", "type": "string"},
    {"name": "locale", "type": "string"},
    {"name": "timezone", "type": "string"},
    {"name": "date_of_birth", "type": "string"},
    {"name": "marketing_opt_in", "type": "bool"},
]
PROFILE_UPDATE_REQUEST = [{**field, "cardinality": "optional"} for field in PROFILE_FIELDS]
ACCOUNT_RESPONSE = [
    {"name": "id", "type": "string"},
    {"name": "username", "type": "string"},
    {"name": "email", "type": "string"},
    {"name": "email_verified", "type": "bool"},
    {"name": "is_active", "type": "bool"},
    {"name": "is_staff", "type": "bool"},
    {"name": "date_joined", "type": "string"},
    {"name": "last_login", "type": "string"},
    # A serializer rather than a name: that is what declares the nested message
    # in the document, and a bare name would leave protoc with an undefined type.
    {"name": "profile", "type": Profile},
]


class AccountService(generics.GenericService):
    """The pair of operations the REST router publishes under `/users`."""

    @grpc_action(request=[], response=ACCOUNT_RESPONSE, response_name="Account")
    @action
    async def Me(self, request: Any, context: Any) -> Any:
        user = require_caller(await grpc_caller(context))
        return _account_message(await sync_to_async(account_service.view)(user))

    @grpc_action(
        request=PROFILE_UPDATE_REQUEST,
        request_name="ProfileUpdate",
        response=ACCOUNT_RESPONSE,
        response_name="Account",
    )
    @action
    async def UpdateProfile(self, request: Any, context: Any) -> Any:
        user = require_caller(await grpc_caller(context))
        changes = {
            field["name"]: getattr(request, field["name"])
            for field in PROFILE_FIELDS
            if request.HasField(field["name"])
        }
        account = await sync_to_async(account_service.update_profile)(user, changes)
        return _account_message(account)


def _account_message(account: AccountView) -> Any:
    from infrastructure.accounts.grpc import accounts_pb2

    return accounts_pb2.Account(
        id=account.id,
        username=account.username,
        email=account.email,
        email_verified=account.email_verified,
        is_active=account.is_active,
        is_staff=account.is_staff,
        date_joined=account.date_joined,
        last_login=account.last_login,
        profile=accounts_pb2.Profile(
            display_name=account.profile.display_name,
            avatar_url=account.profile.avatar_url,
            bio=account.profile.bio,
            locale=account.profile.locale,
            timezone=account.profile.timezone,
            date_of_birth=account.profile.date_of_birth,
            marketing_opt_in=account.profile.marketing_opt_in,
        ),
    )


GRPC_SERVICES = [AccountService]
