"""microsoft sign-in, over gRPC.

The redirect half of the flow is the client's to perform -- open the URL, let
the person approve, collect the callback. What is here is the two ends of it,
and what a completed login yields is a credential rather than a session cookie.
"""

from typing import Any

from asgiref.sync import sync_to_async
from django_socio_grpc import generics
from django_socio_grpc.decorators import grpc_action

from infrastructure.common.errors import ApiError
from infrastructure.common.grpc.errors import action
from infrastructure.common.responses import ResponseTitle
from infrastructure.oauth.core.grpc.messages import CREDENTIALS_RESPONSE, credentials_message
from infrastructure.oauth.core.grpc.social import (
    COMPLETE_REQUEST,
    START_RESPONSE,
    callback_data,
)
from infrastructure.oauth.core.social import OAuthProviderError, social_login_service
from infrastructure.oauth.microsoft.provider import provider


def _pb2() -> Any:
    from infrastructure.oauth.microsoft.grpc import oauth_microsoft_pb2

    return oauth_microsoft_pb2


def _begin(request_object: Any) -> Any:
    try:
        return social_login_service.begin(request_object, provider)
    except OAuthProviderError as error:
        raise ApiError(str(error), status=503, title=ResponseTitle.OAUTH_NOT_CONFIGURED) from error


def _complete(data: dict[str, str], binding: str) -> Any:
    try:
        return social_login_service.complete(provider, data, binding=binding)
    except OAuthProviderError as error:
        raise ApiError(str(error), status=400, title=ResponseTitle.OAUTH_FAILED) from error


class MicrosoftOAuthService(generics.GenericService):
    """Begin and finish a microsoft sign-in."""

    @grpc_action(request=[], response=START_RESPONSE, response_name="Authorization")
    @action
    async def Start(self, request: Any, context: Any) -> Any:
        started = await sync_to_async(_begin)(context.http_request)
        # `Authorization`, not `Start`: proto3 puts messages and rpc methods in
        # one namespace, and this service publishes a `Start` rpc.
        return _pb2().Authorization(
            authorization_url=started.authorization_url,
            binding=started.binding,
            expires_in=started.expires_in,
        )

    @grpc_action(
        request=COMPLETE_REQUEST,
        request_name="CompleteRequest",
        response=CREDENTIALS_RESPONSE,
        response_name="Credentials",
    )
    @action
    async def Complete(self, request: Any, context: Any) -> Any:
        from infrastructure.auth.core.flows import record_event
        from infrastructure.auth.core.models import AuthEventType
        from infrastructure.auth.core.sessions import issue_credentials

        data = callback_data(request)
        completed = await sync_to_async(_complete)(data, request.binding)
        credentials = await sync_to_async(issue_credentials)(
            context.http_request, completed.user, method=completed.method
        )
        await sync_to_async(record_event)(
            context.http_request,
            AuthEventType.LOGIN_SUCCEEDED,
            user=completed.user,
            method=completed.method,
        )
        return credentials_message(credentials, _pb2())


GRPC_SERVICES = [MicrosoftOAuthService]
