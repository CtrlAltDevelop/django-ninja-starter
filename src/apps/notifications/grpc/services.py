"""Reading notifications over gRPC: the history, the badge, and the tray.

Scoped to the caller by the service, not by a request field: there is no
notification call that takes a user id, so the only account a call can read is
the one whose credential its metadata carried.

The calls mirror the REST endpoints and the socket commands one for one.
"""

import json
from typing import Any
from uuid import UUID

from asgiref.sync import sync_to_async
from django_socio_grpc import generics
from django_socio_grpc.decorators import grpc_action

from apps.notifications.grpc.serializers import Notification
from apps.notifications.services import DEFAULT_PAGE, NotificationNotFound, notification_service
from infrastructure.common.errors import ApiError
from infrastructure.common.grpc.errors import action, require_caller
from infrastructure.common.identity import grpc_caller
from infrastructure.common.responses import ResponseTitle

# Three-valued on purpose: unset means "everything", which is not the same
# question as `unread = false`. Shared by List and Count, which take the same
# filters for the same reason the REST endpoints do.
FILTER_FIELDS = [
    {"name": "unread", "cardinality": "optional", "type": "bool"},
    {"name": "level", "type": "string"},
    {"name": "audience", "type": "string"},
    {"name": "include_dismissed", "type": "bool"},
]

READ_RESULT_FIELDS = [
    {"name": "id", "type": "string"},
    {"name": "unread", "type": "int32"},
    {"name": "changed", "type": "bool"},
]


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
        dismissed=row["dismissed"],
    )


def _filters(request: Any) -> dict[str, Any]:
    return {
        "unread": request.unread if request.HasField("unread") else None,
        "level": request.level or None,
        "audience": request.audience or None,
        "include_dismissed": request.include_dismissed,
    }


class NotificationService(generics.GenericService):
    """The same calls the REST router publishes under `/notifications`."""

    @grpc_action(
        request=[
            *FILTER_FIELDS,
            {"name": "limit", "type": "int32"},
            {"name": "offset", "type": "int32"},
        ],
        request_name="ListRequest",
        response=[
            {"name": "notifications", "cardinality": "repeated", "type": Notification},
            {"name": "total", "type": "int32"},
            {"name": "limit", "type": "int32"},
            {"name": "offset", "type": "int32"},
        ],
        response_name="NotificationList",
    )
    @action
    async def List(self, request: Any, context: Any) -> Any:
        user = require_caller(await grpc_caller(context))
        filters = _filters(request)
        limit = request.limit or DEFAULT_PAGE
        rows = await sync_to_async(notification_service.list)(
            user, limit=limit, offset=request.offset, **filters
        )
        total = await sync_to_async(notification_service.count)(user, **filters)
        return _pb2().NotificationList(
            notifications=[_notification(row) for row in rows],
            total=total,
            limit=limit,
            offset=request.offset,
        )

    @grpc_action(
        request=[{"name": "notification_id", "type": "string"}],
        request_name="GetRequest",
        response=Notification,
        response_name="Notification",
    )
    @action
    async def Get(self, request: Any, context: Any) -> Any:
        user = require_caller(await grpc_caller(context))
        row = await self._resolve(user, request.notification_id, "get")
        return _notification(row)

    @grpc_action(
        request=FILTER_FIELDS,
        request_name="CountRequest",
        response=[{"name": "count", "type": "int32"}],
        response_name="CountResult",
    )
    @action
    async def Count(self, request: Any, context: Any) -> Any:
        user = require_caller(await grpc_caller(context))
        count = await sync_to_async(notification_service.count)(user, **_filters(request))
        return _pb2().CountResult(count=count)

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
        request=[],
        response=[
            {"name": "count", "type": "int32"},
            {"name": "unread", "type": "int32"},
        ],
        response_name="DismissAllResult",
    )
    @action
    async def DismissAll(self, request: Any, context: Any) -> Any:
        user = require_caller(await grpc_caller(context))
        result = await sync_to_async(notification_service.dismiss_all)(user)
        return _pb2().DismissAllResult(count=result["count"], unread=result["unread"])

    @grpc_action(
        request=[{"name": "notification_id", "type": "string"}],
        request_name="ReadRequest",
        response=READ_RESULT_FIELDS,
        response_name="ReadResult",
    )
    @action
    async def Read(self, request: Any, context: Any) -> Any:
        user = require_caller(await grpc_caller(context))
        return _pb2().ReadResult(**await self._resolve(user, request.notification_id, "mark_read"))

    @grpc_action(
        request=[{"name": "notification_id", "type": "string"}],
        request_name="UnreadRequest",
        response=READ_RESULT_FIELDS,
        response_name="UnreadResult",
    )
    @action
    async def Unread(self, request: Any, context: Any) -> Any:
        user = require_caller(await grpc_caller(context))
        return _pb2().UnreadResult(
            **await self._resolve(user, request.notification_id, "mark_unread")
        )

    @grpc_action(
        request=[{"name": "notification_id", "type": "string"}],
        request_name="DismissRequest",
        response=READ_RESULT_FIELDS,
        response_name="DismissResult",
    )
    @action
    async def Dismiss(self, request: Any, context: Any) -> Any:
        user = require_caller(await grpc_caller(context))
        return _pb2().DismissResult(**await self._resolve(user, request.notification_id, "dismiss"))

    @grpc_action(
        request=[{"name": "notification_id", "type": "string"}],
        request_name="RestoreRequest",
        response=READ_RESULT_FIELDS,
        response_name="RestoreResult",
    )
    @action
    async def Restore(self, request: Any, context: Any) -> Any:
        user = require_caller(await grpc_caller(context))
        return _pb2().RestoreResult(**await self._resolve(user, request.notification_id, "restore"))

    async def _resolve(self, user: Any, notification_id: str, method: str) -> Any:
        """Run one of the service's per-notification calls, translating its refusal.

        A malformed id and an id belonging to somebody else are the same answer,
        since saying which would confirm the existence of another account's mail.
        """
        try:
            result = await sync_to_async(getattr(notification_service, method))(
                user, UUID(notification_id)
            )
        except (NotificationNotFound, ValueError) as error:
            raise ApiError(
                "No such notification.", status=404, title=ResponseTitle.NOT_FOUND
            ) from error
        if method == "get":
            return result
        return {
            "id": str(result["id"]),
            "unread": result["unread"],
            "changed": result["changed"],
        }


GRPC_SERVICES = [NotificationService]
