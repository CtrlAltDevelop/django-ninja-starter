"""Magic-link authentication, over gRPC.

Asking for a link returns nothing that could redeem it. The token travels in the
message that was sent and comes back through ``Verify`` -- which is what a
landing page does with it over HTTP too.
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
from infrastructure.auth.magic_link.services import magic_link_service
from infrastructure.common.grpc.errors import action

START_REQUEST = [{"name": "email", "type": "string"}]
LINK_SENT_RESPONSE = [
    {"name": "detail", "type": "string"},
    {"name": "destination", "type": "string"},
    {"name": "expires_in", "type": "int32"},
]


def _pb2() -> Any:
    from infrastructure.auth.magic_link.grpc import auth_magic_link_pb2

    return auth_magic_link_pb2


def _link_sent(sent: Any) -> Any:
    return _pb2().LinkSent(
        detail=sent.detail, destination=sent.destination, expires_in=sent.expires_in
    )


class MagicLinkService(generics.GenericService):
    """Ask for a link, redeem one, and sign out."""

    @grpc_action(
        request=START_REQUEST,
        request_name="SignupStartRequest",
        response=LINK_SENT_RESPONSE,
        response_name="LinkSent",
    )
    @action
    async def SignupStart(self, request: Any, context: Any) -> Any:
        sent = await sync_to_async(magic_link_service.start_signup)(
            context.http_request, email=request.email
        )
        return _link_sent(sent)

    @grpc_action(
        request=START_REQUEST,
        request_name="LoginStartRequest",
        response=LINK_SENT_RESPONSE,
        response_name="LinkSent",
    )
    @action
    async def LoginStart(self, request: Any, context: Any) -> Any:
        sent = await sync_to_async(magic_link_service.start_login)(
            context.http_request, email=request.email
        )
        return _link_sent(sent)

    @grpc_action(
        request=[{"name": "token", "type": "string"}],
        request_name="VerifyRequest",
        response=LOGIN_RESPONSE,
        response_name="LoginResult",
    )
    @action
    async def Verify(self, request: Any, context: Any) -> Any:
        result = await sync_to_async(magic_link_service.verify)(
            context.http_request, token=request.token
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
        detail = await sync_to_async(magic_link_service.logout)(
            context.http_request, token=request.token
        )
        return _pb2().Message(detail=detail)


GRPC_SERVICES = [MagicLinkService]
