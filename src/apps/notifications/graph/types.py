"""GraphQL types for the notifications addressed to an account.

``read`` and ``dismissed`` are the fields worth pointing at: both are
per-account, and both are computed rather than stored on the row, because a
global notification is read and cleared away by each person separately. Two
clients signed in as different people can be looking at the same notification id
with different values for either, and both are correct.
"""

from typing import Any

import strawberry
from strawberry.scalars import JSON


@strawberry.type
class NotificationType:
    """One notification, as this account sees it."""

    id: str
    audience: str
    subject: str
    body: str
    level: str
    link: str
    data: JSON
    created_at: str
    read: bool
    dismissed: bool


@strawberry.type
class NotificationPageType:
    """A page of notifications, and how many there were to page through."""

    notifications: list[NotificationType]
    total: int
    limit: int
    offset: int


@strawberry.type
class ReadType:
    """Confirmation, with the badge number a client would otherwise refetch for."""

    id: str
    unread: int
    changed: bool


@strawberry.type
class ReadAllType:
    count: int
    unread: int


def notification_type(notification: dict[str, Any]) -> NotificationType:
    return NotificationType(
        id=str(notification["id"]),
        audience=str(notification["audience"]),
        subject=notification["subject"],
        body=notification["body"],
        level=str(notification["level"]),
        link=notification["link"],
        data=notification.get("data", {}),
        # Already an ISO string: `payload` renders it once, for the socket and
        # for every transport that reads the same history.
        created_at=notification["created_at"],
        read=notification["read"],
        dismissed=notification["dismissed"],
    )
