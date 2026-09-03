"""GraphQL types for the notifications addressed to an account.

``read`` is the field worth pointing at: it is per-account, and it is computed
rather than stored on the row, because a global notification is read by each
person separately. Two clients signed in as different people can be looking at
the same notification id with different ``read`` values, and both are correct.
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


@strawberry.type
class ReadType:
    """Confirmation, with the badge number a client would otherwise refetch for."""

    id: str
    unread: int


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
    )
