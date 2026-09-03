"""The notifications app's contribution to the project's GraphQL schema.

Everything is scoped to the caller by the service, not by an argument: there is
no field that takes a user id, so the only account a query can read is the one
whose credential it carried.
"""

from typing import Any
from uuid import UUID

import strawberry
from strawberry.types import Info

from apps.notifications.graph.types import (
    NotificationType,
    ReadAllType,
    ReadType,
    notification_type,
)
from apps.notifications.services import NotificationNotFound, notification_service
from infrastructure.common.errors import ApiError
from infrastructure.common.graph.errors import require_caller, resolver
from infrastructure.common.identity import caller
from infrastructure.common.responses import ResponseTitle


def _caller(info: Info[Any, Any]) -> Any:
    return require_caller(caller(info.context.request))


@strawberry.type
class Query:
    @strawberry.field(description="Notifications this account can see, newest first.")
    @resolver
    def notifications(
        self,
        info: Info[Any, Any],
        unread: bool | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[NotificationType]:
        rows = notification_service.list(_caller(info), unread=unread, limit=limit, offset=offset)
        return [notification_type(row) for row in rows]

    @strawberry.field(description="The badge number, without the list it counts.")
    @resolver
    def unread_notification_count(self, info: Info[Any, Any]) -> int:
        return notification_service.unread_count(_caller(info))


@strawberry.type
class Mutation:
    @strawberry.mutation(description="Mark every notification read.")
    @resolver
    def read_all_notifications(self, info: Info[Any, Any]) -> ReadAllType:
        result = notification_service.mark_all_read(_caller(info))
        return ReadAllType(count=result["count"], unread=result["unread"])

    @strawberry.mutation(description="Mark one notification read.")
    @resolver
    def read_notification(self, info: Info[Any, Any], notification_id: str) -> ReadType:
        try:
            result = notification_service.mark_read(_caller(info), UUID(notification_id))
        except (NotificationNotFound, ValueError) as error:
            raise ApiError(
                "No such notification.", status=404, title=ResponseTitle.NOT_FOUND
            ) from error
        return ReadType(id=str(result["id"]), unread=result["unread"])
