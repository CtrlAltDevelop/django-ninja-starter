"""Password authentication, over gRPC.

The same service the router and the resolver call, so the same rules apply. One
thing is worth saying out loud: signing in over gRPC only produces a usable
credential under a token mode, because a gRPC call has nowhere to keep a session
cookie. Under ``DJANGO_AUTH_TOKEN_MODE=none`` the reply's ``credentials`` say so
-- ``token_type`` is ``session`` and the tokens are empty.
"""

from typing import Any

from asgiref.sync import sync_to_async
from django_socio_grpc import generics
from django_socio_grpc.decorators import grpc_action

from infrastructure.auth.core.grpc.messages import (
    LOGIN_RESPONSE,
    MESSAGE_RESPONSE,
    login_message,
)
from infrastructure.auth.password.services import password_service
from infrastructure.common.grpc.errors import action, require_caller
from infrastructure.common.identity import grpc_caller

RESET_TICKET_RESPONSE = [
    {"name": "detail", "type": "string"},
    {"name": "ticket", "type": "string"},
    {"name": "expires_in", "type": "int32"},
]


def _pb2() -> Any:
    from infrastructure.auth.password.grpc import auth_password_pb2

    return auth_password_pb2


class PasswordService(generics.GenericService):
    """Sign up, sign in, sign out, reset and change -- with a password."""

    @grpc_action(
        request=[
            {"name": "identifier", "type": "string"},
            {"name": "password", "type": "string"},
            {"name": "email", "type": "string"},
        ],
        request_name="SignupRequest",
        response=LOGIN_RESPONSE,
        response_name="LoginResult",
    )
    @action
    async def Signup(self, request: Any, context: Any) -> Any:
        result = await sync_to_async(password_service.signup)(
            context.http_request,
            identifier=request.identifier,
            password=request.password,
            email=request.email,
        )
        return login_message(result, _pb2())

    @grpc_action(
        request=[
            {"name": "identifier", "type": "string"},
            {"name": "password", "type": "string"},
        ],
        request_name="LoginRequest",
        response=LOGIN_RESPONSE,
        response_name="LoginResult",
    )
    @action
    async def Login(self, request: Any, context: Any) -> Any:
        result = await sync_to_async(password_service.login)(
            context.http_request, identifier=request.identifier, password=request.password
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
        detail = await sync_to_async(password_service.logout)(
            context.http_request, token=request.token
        )
        return _pb2().Message(detail=detail)

    @grpc_action(
        request=[{"name": "email", "type": "string"}],
        request_name="ForgotRequest",
        response=RESET_TICKET_RESPONSE,
        response_name="ResetTicket",
    )
    @action
    async def Forgot(self, request: Any, context: Any) -> Any:
        ticket = await sync_to_async(password_service.forgot)(
            context.http_request, email=request.email
        )
        return _pb2().ResetTicket(
            detail=ticket.detail, ticket=ticket.ticket, expires_in=ticket.expires_in
        )

    @grpc_action(
        request=[
            {"name": "ticket", "type": "string"},
            {"name": "code", "type": "string"},
            {"name": "password", "type": "string"},
        ],
        request_name="ResetRequest",
        response=MESSAGE_RESPONSE,
        response_name="Message",
    )
    @action
    async def Reset(self, request: Any, context: Any) -> Any:
        detail = await sync_to_async(password_service.reset)(
            context.http_request,
            ticket=request.ticket,
            code=request.code,
            password=request.password,
        )
        return _pb2().Message(detail=detail)

    @grpc_action(
        request=[
            {"name": "current_password", "type": "string"},
            {"name": "new_password", "type": "string"},
        ],
        request_name="ChangeRequest",
        response=MESSAGE_RESPONSE,
        response_name="Message",
    )
    @action
    async def Change(self, request: Any, context: Any) -> Any:
        user = require_caller(await grpc_caller(context))
        detail = await sync_to_async(password_service.change)(
            context.http_request,
            user,
            current_password=request.current_password,
            new_password=request.new_password,
        )
        return _pb2().Message(detail=detail)


GRPC_SERVICES = [PasswordService]
