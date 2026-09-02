"""The contract the notification endpoints publish.

``read`` is the field worth pointing at: it is per-account, and it is computed
rather than stored on the row, because a global notification is read by each
person separately. Two clients signed in as different people can therefore be
looking at the same notification id with different ``read`` values, and both are
correct.
"""

from datetime import datetime
from typing import Any
from uuid import UUID

from ninja import Schema

from apps.notifications.models import Audience, Level


class NotificationOut(Schema):
    """One notification, as this account sees it."""

    id: UUID
    audience: Audience
    subject: str
    body: str
    level: Level
    link: str
    data: dict[str, Any] = {}
    created_at: datetime
    read: bool


class UnreadCountOut(Schema):
    count: int


class ReadOut(Schema):
    """Confirmation, with the badge number a client would otherwise refetch for."""

    id: UUID
    unread: int


class ReadAllOut(Schema):
    count: int
    """How many were still unread. Zero means there was nothing to do."""

    unread: int
