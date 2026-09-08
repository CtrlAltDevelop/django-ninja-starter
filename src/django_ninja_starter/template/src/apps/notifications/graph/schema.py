"""The notifications app's contribution to the project's GraphQL schema.

Everything is scoped to the caller by the service, not by an argument: there is
no field that takes a user id, so the only account a query can read is the one
whose credential it carried.

The fields mirror the REST endpoints and the socket commands one for one, so a
project that publishes all three publishes one contract three ways rather than
three contracts.
"""

from typing import Any
from uuid import UUID

import strawberry
from strawberry.types import Info

from apps.notifications.graph.types import (
    NotificationPageType,
    NotificationType,
    ReadAllType,
    ReadType,
    notification_type,
)
from apps.notifications.services import DEFAULT_PAGE, NotificationNotFound, notification_service
from infrastructure.common.errors import ApiError
from infrastructure.common.graph.errors import require_caller, resolver
from infrastructure.common.identity import caller
from infrastructure.common.responses import ResponseTitle


def _caller(info: Info[Any, Any]) -> Any:
    return require_caller(caller(info.context.request))


def _change(info: Info[Any, Any], notification_id: str, method: str) -> ReadType:
    """Run one of the service's single-notification changes, translating its refusal."""
    try:
        result = getattr(notification_service, method)(_caller(info), UUID(notification_id))
    except (NotificationNotFound, ValueError) as error:
        raise ApiError(
            "No such notification.", status=404, title=ResponseTitle.NOT_FOUND
        ) from error
    return ReadType(id=str(result["id"]), unread=result["unread"], changed=result["changed"])


@strawberry.type
class Query:
    @strawberry.field(description="Notifications this account can see, newest first.")
    @resolver
    def notifications(
        self,
        info: Info[Any, Any],
        unread: bool | None = None,
        level: str | None = None,
        audience: str | None = None,
        include_dismissed: bool = False,
        limit: int = DEFAULT_PAGE,
        offset: int = 0,
    ) -> NotificationPageType:
        filters: dict[str, Any] = {
            "unread": unread,
            "level": level,
            "audience": audience,
            "include_dismissed": include_dismissed,
        }
        user = _caller(info)
        rows = notification_service.list(user, limit=limit, offset=offset, **filters)
        return NotificationPageType(
            notifications=[notification_type(row) for row in rows],
            total=notification_service.count(user, **filters),
            limit=limit,
            offset=offset,
        )

    @strawberry.field(description="One notification by id, dismissed or not.")
    @resolver
    def notification(self, info: Info[Any, Any], notification_id: str) -> NotificationType:
        try:
            row = notification_service.get(_caller(info), UUID(notification_id))
        except (NotificationNotFound, ValueError) as error:
            raise ApiError(
                "No such notification.", status=404, title=ResponseTitle.NOT_FOUND
            ) from error
        return notification_type(row)

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

    @strawberry.mutation(description="Clear every notification out of this account's tray.")
    @resolver
    def dismiss_all_notifications(self, info: Info[Any, Any]) -> ReadAllType:
        result = notification_service.dismiss_all(_caller(info))
        return ReadAllType(count=result["count"], unread=result["unread"])

    @strawberry.mutation(description="Mark one notification read.")
    @resolver
    def read_notification(self, info: Info[Any, Any], notification_id: str) -> ReadType:
        return _change(info, notification_id, "mark_read")

    @strawberry.mutation(description="Undo a read.")
    @resolver
    def unread_notification(self, info: Info[Any, Any], notification_id: str) -> ReadType:
        return _change(info, notification_id, "mark_unread")

    @strawberry.mutation(description="Take one notification out of this account's tray.")
    @resolver
    def dismiss_notification(self, info: Info[Any, Any], notification_id: str) -> ReadType:
        return _change(info, notification_id, "dismiss")

    @strawberry.mutation(description="Put a dismissed notification back in the tray.")
    @resolver
    def restore_notification(self, info: Info[Any, Any], notification_id: str) -> ReadType:
        return _change(info, notification_id, "restore")
