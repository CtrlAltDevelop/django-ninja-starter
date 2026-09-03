"""Reading notifications over gRPC: the list, the badge, and marking them read.

Scoped to the caller by the service, not by a request field: there is no
notification call that takes a user id, so the only account a call can read is
the one whose credential its metadata carried.
"""

import json
from typing import Any
from uuid import UUID

from asgiref.sync import sync_to_async
from django_socio_grpc import generics
from django_socio_grpc.decorators import grpc_action

from apps.notifications.grpc.serializers import Notification
from apps.notifications.services import NotificationNotFound, notification_service
from infrastructure.common.errors import ApiError
from infrastructure.common.grpc.errors import action, require_caller
from infrastructure.common.identity import grpc_caller
from infrastructure.common.responses import ResponseTitle


def _pb2() -> Any:
    from apps.notifications.grpc import notifications_pb2

    return notifications_pb2


def _notification(row: dict[str, Any]) -> Any:
    return _pb2().Notification(
        id=str(row["id"]),
        audience=str(row["audience"]),
        subject=row["subject"],
        body=row["body"],
        level=str(row["level"]),
        link=row["link"],
        data_json=json.dumps(row.get("data", {}), default=str),
        # Already an ISO string: `payload` renders it once, for every reader.
        created_at=row["created_at"],
        read=row["read"],
    )


class NotificationService(generics.GenericService):
    """The four calls the REST router publishes under `/notifications`."""

    @grpc_action(
        request=[
            # Three-valued on purpose: unset means "everything", which is not the
            # same question as `unread = false`.
            {"name": "unread", "cardinality": "optional", "type": "bool"},
            {"name": "limit", "type": "int32"},
            {"name": "offset", "type": "int32"},
        ],
        request_name="ListRequest",
        response=[{"name": "notifications", "cardinality": "repeated", "type": Notification}],
        response_name="NotificationList",
    )
    @action
    async def List(self, request: Any, context: Any) -> Any:
        user = require_caller(await grpc_caller(context))
        rows = await sync_to_async(notification_service.list)(
            user,
            unread=request.unread if request.HasField("unread") else None,
            limit=request.limit or 50,
            offset=request.offset,
        )
        return _pb2().NotificationList(notifications=[_notification(row) for row in rows])

    @grpc_action(
        request=[],
        response=[{"name": "count", "type": "int32"}],
        # `UnreadCountResult`, not `UnreadCount`: proto3 puts messages and rpc
        # methods in one namespace, and this service publishes an `UnreadCount` rpc.
        response_name="UnreadCountResult",
    )
    @action
    async def UnreadCount(self, request: Any, context: Any) -> Any:
        user = require_caller(await grpc_caller(context))
        count = await sync_to_async(notification_service.unread_count)(user)
        return _pb2().UnreadCountResult(count=count)

    @grpc_action(
        request=[],
        response=[
            {"name": "count", "type": "int32"},
            {"name": "unread", "type": "int32"},
        ],
        response_name="ReadAllResult",
    )
    @action
    async def ReadAll(self, request: Any, context: Any) -> Any:
        user = require_caller(await grpc_caller(context))
        result = await sync_to_async(notification_service.mark_all_read)(user)
        return _pb2().ReadAllResult(count=result["count"], unread=result["unread"])

    @grpc_action(
        request=[{"name": "notification_id", "type": "string"}],
        request_name="ReadRequest",
        response=[
            {"name": "id", "type": "string"},
            {"name": "unread", "type": "int32"},
        ],
        response_name="ReadResult",
    )
    @action
    async def Read(self, request: Any, context: Any) -> Any:
        user = require_caller(await grpc_caller(context))
        try:
            result = await sync_to_async(notification_service.mark_read)(
                user, UUID(request.notification_id)
            )
        except (NotificationNotFound, ValueError) as error:
            raise ApiError(
                "No such notification.", status=404, title=ResponseTitle.NOT_FOUND
            ) from error
        return _pb2().ReadResult(id=str(result["id"]), unread=result["unread"])


GRPC_SERVICES = [NotificationService]
