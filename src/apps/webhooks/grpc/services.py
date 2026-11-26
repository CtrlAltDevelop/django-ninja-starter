"""Managing webhook endpoints over gRPC, mirroring the REST endpoints one for one."""

from typing import Any

from asgiref.sync import sync_to_async
from django_socio_grpc import generics
from django_socio_grpc.decorators import grpc_action

from apps.webhooks import services
from apps.webhooks.grpc.serializers import WebhookDelivery, WebhookEndpoint
from infrastructure.common.grpc.errors import action, require_caller
from infrastructure.common.identity import grpc_caller


def _pb2() -> Any:
    from apps.webhooks.grpc import webhooks_pb2

    return webhooks_pb2


def _endpoint(row: dict[str, Any]) -> Any:
    return _pb2().WebhookEndpoint(**row)


def _delivery(row: dict[str, Any]) -> Any:
    pb2 = _pb2()
    attempts = [pb2.WebhookAttempt(**attempt) for attempt in row["attempt_log"]]
    return pb2.WebhookDelivery(**{**row, "attempt_log": attempts})


class WebhookService(generics.GenericService):
    """The same calls the REST router publishes under `/webhooks`."""

    @grpc_action(
        request=[],
        response=[{"name": "endpoints", "cardinality": "repeated", "type": WebhookEndpoint}],
        response_name="EndpointList",
    )
    @action
    async def List(self, request: Any, context: Any) -> Any:
        user = require_caller(await grpc_caller(context))
        rows = await sync_to_async(services.list_endpoints)(user)
        return _pb2().EndpointList(endpoints=[_endpoint(row) for row in rows])

    @grpc_action(
        request=[
            {"name": "url", "type": "string"},
            {"name": "events", "cardinality": "repeated", "type": "string"},
        ],
        request_name="CreateRequest",
        response=WebhookEndpoint,
        response_name="WebhookEndpoint",
    )
    @action
    async def Create(self, request: Any, context: Any) -> Any:
        user = require_caller(await grpc_caller(context))
        row = await sync_to_async(services.create_endpoint)(user, request.url, list(request.events))
        return _endpoint(row)

    @grpc_action(
        request=[{"name": "endpoint_id", "type": "string"}],
        request_name="DeleteRequest",
        response=[{"name": "deleted", "type": "bool"}],
        response_name="DeleteResult",
    )
    @action
    async def Delete(self, request: Any, context: Any) -> Any:
        user = require_caller(await grpc_caller(context))
        await sync_to_async(services.delete_endpoint)(user, request.endpoint_id)
        return _pb2().DeleteResult(deleted=True)

    @grpc_action(
        request=[{"name": "endpoint_id", "type": "string"}],
        request_name="RotateSecretRequest",
        response=WebhookEndpoint,
        response_name="WebhookEndpoint",
    )
    @action
    async def RotateSecret(self, request: Any, context: Any) -> Any:
        user = require_caller(await grpc_caller(context))
        return _endpoint(await sync_to_async(services.rotate_secret)(user, request.endpoint_id))

    @grpc_action(
        request=[
            {"name": "endpoint_id", "type": "string"},
            {"name": "limit", "type": "int32"},
        ],
        request_name="ListDeliveriesRequest",
        response=[{"name": "deliveries", "cardinality": "repeated", "type": WebhookDelivery}],
        response_name="DeliveryList",
    )
    @action
    async def ListDeliveries(self, request: Any, context: Any) -> Any:
        user = require_caller(await grpc_caller(context))
        rows = await sync_to_async(services.list_deliveries)(
            user, request.endpoint_id, request.limit or 50
        )
        return _pb2().DeliveryList(deliveries=[_delivery(row) for row in rows])


GRPC_SERVICES = [WebhookService]
