"""email-code authentication, over gRPC.

The same service the router and the resolver call. As with every login in this
project, the credential a reply carries is only useful under a token mode: a
gRPC call has nowhere to keep a session cookie.
"""

from typing import Any

from asgiref.sync import sync_to_async
from django_socio_grpc import generics
from django_socio_grpc.decorators import grpc_action

from infrastructure.auth.core.grpc.messages import (
    CHALLENGE_RESPONSE,
    LOGIN_RESPONSE,
    MESSAGE_RESPONSE,
    login_message,
)
from infrastructure.auth.email_code.services import email_code_service
from infrastructure.common.grpc.errors import action

START_REQUEST = [{"name": "email", "type": "string"}]
VERIFY_REQUEST = [
    {"name": "ticket", "type": "string"},
    {"name": "code", "type": "string"},
]


def _pb2() -> Any:
    from infrastructure.auth.email_code.grpc import auth_email_code_pb2

    return auth_email_code_pb2


def _challenge(challenge: Any) -> Any:
    return _pb2().Challenge(
        ticket=challenge.ticket,
        channel=challenge.channel,
        destination=challenge.destination,
        expires_in=challenge.expires_in,
    )


class EmailCodeService(generics.GenericService):
    """Sign up, sign in and sign out with a one-time code."""

    @grpc_action(
        request=START_REQUEST,
        request_name="SignupStartRequest",
        response=CHALLENGE_RESPONSE,
        response_name="Challenge",
    )
    @action
    async def SignupStart(self, request: Any, context: Any) -> Any:
        challenge = await sync_to_async(email_code_service.start_signup)(
            context.http_request, email=request.email
        )
        return _challenge(challenge)

    @grpc_action(
        request=VERIFY_REQUEST,
        request_name="SignupVerifyRequest",
        response=LOGIN_RESPONSE,
        response_name="LoginResult",
    )
    @action
    async def SignupVerify(self, request: Any, context: Any) -> Any:
        result = await sync_to_async(email_code_service.verify_signup)(
            context.http_request, ticket=request.ticket, code=request.code
        )
        return login_message(result, _pb2())

    @grpc_action(
        request=START_REQUEST,
        request_name="LoginStartRequest",
        response=CHALLENGE_RESPONSE,
        response_name="Challenge",
    )
    @action
    async def LoginStart(self, request: Any, context: Any) -> Any:
        challenge = await sync_to_async(email_code_service.start_login)(
            context.http_request, email=request.email
        )
        return _challenge(challenge)

    @grpc_action(
        request=VERIFY_REQUEST,
        request_name="LoginVerifyRequest",
        response=LOGIN_RESPONSE,
        response_name="LoginResult",
    )
    @action
    async def LoginVerify(self, request: Any, context: Any) -> Any:
        result = await sync_to_async(email_code_service.verify_login)(
            context.http_request, ticket=request.ticket, code=request.code
        )
        return login_message(result, _pb2())

    @grpc_action(
        request=[{"name": "token", "type": "string"}],
        request_name="LogoutRequest",
        response=MESSAGE_RESPONSE,
        response_name="Message",
    )
    @action
    async def Logout(self, request: Any, context: Any) -> Any:
        detail = await sync_to_async(email_code_service.logout)(
            context.http_request, token=request.token
        )
        return _pb2().Message(detail=detail)


GRPC_SERVICES = [EmailCodeService]
