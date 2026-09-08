"""The token operations for the sliding mode, over gRPC.

The four actions every mode publishes, bound to this mode's service.

Registered only when this mode is the *active* one. A deployment may install all
three apps -- ``DJANGO_OAUTH_MODE=all`` does -- but only one of them answers at
``/auth/token``, and only one should answer over gRPC either.
"""

from typing import Any

from asgiref.sync import sync_to_async
from django.conf import settings
from django_socio_grpc import generics
from django_socio_grpc.decorators import grpc_action

from infrastructure.common.grpc.errors import action, require_caller
from infrastructure.common.identity import grpc_caller
from infrastructure.oauth.core.grpc.messages import (
    CREDENTIALS_RESPONSE,
    END_SESSION_REQUEST,
    MESSAGE_RESPONSE,
    REFRESH_REQUEST,
    REVOKE_REQUEST,
    SESSION_LIST_RESPONSE,
    credentials_message,
    session_list_message,
)
from infrastructure.oauth.sliding.services import token_service


def _pb2() -> Any:
    from infrastructure.oauth.sliding.grpc import oauth_sliding_pb2

    return oauth_sliding_pb2


class SlidingTokenServiceRpc(generics.GenericService):
    """Refresh, revoke, list and end -- for the sliding mode."""

    @grpc_action(
        request=REFRESH_REQUEST,
        request_name="RefreshRequest",
        response=CREDENTIALS_RESPONSE,
        response_name="Credentials",
    )
    @action
    async def Refresh(self, request: Any, context: Any) -> Any:
        credentials = await sync_to_async(token_service.refresh)(
            context.http_request, refresh_token=request.refresh_token
        )
        return credentials_message(credentials, _pb2())

    @grpc_action(
        request=REVOKE_REQUEST,
        request_name="RevokeRequest",
        response=MESSAGE_RESPONSE,
        response_name="Message",
    )
    @action
    async def Revoke(self, request: Any, context: Any) -> Any:
        detail = await sync_to_async(token_service.revoke)(
            context.http_request, token=request.token
        )
        return _pb2().Message(detail=detail)

    @grpc_action(request=[], response=SESSION_LIST_RESPONSE, response_name="SessionList")
    @action
    async def Sessions(self, request: Any, context: Any) -> Any:
        user = require_caller(await grpc_caller(context))
        return session_list_message(await sync_to_async(token_service.sessions)(user), _pb2())

    @grpc_action(
        request=END_SESSION_REQUEST,
        request_name="EndSessionRequest",
        response=MESSAGE_RESPONSE,
        response_name="Message",
    )
    @action
    async def EndSession(self, request: Any, context: Any) -> Any:
        user = require_caller(await grpc_caller(context))
        detail = await sync_to_async(token_service.end_session)(user, request.session_id)
        return _pb2().Message(detail=detail)


GRPC_SERVICES = [SlidingTokenServiceRpc]
# The proto is generated whichever mode is active -- the committed stubs must not
# go stale because a deployment happens to be running one of the other two -- but
# only the active mode answers, exactly as only one mode is mounted at
# `/auth/token`.
GRPC_SERVED = settings.AUTH_TOKEN_MODE == "sliding"
