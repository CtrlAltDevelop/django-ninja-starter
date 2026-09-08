"""The contract the notification endpoints publish.

``read`` and ``dismissed`` are the fields worth pointing at: both are
per-account, and both are computed rather than stored on the row, because a
global notification is read and cleared away by each person separately. Two
clients signed in as different people can therefore be looking at the same
notification id with different values for either, and both are correct.
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
    dismissed: bool


class NotificationPage(Schema):
    """A page of notifications, and how many there were to page through."""

    notifications: list[NotificationOut]
    total: int
    """Matching the same filters, ignoring ``limit`` and ``offset``."""

    limit: int
    offset: int


class UnreadCountOut(Schema):
    count: int


class ReadOut(Schema):
    """Confirmation, with the badge number a client would otherwise refetch for."""

    id: UUID
    unread: int
    changed: bool
    """False when it was already in that state. Success either way -- marking a
    read notification read is not an error, and a client that fired twice should
    not have to care which of the two arrived first."""


class ReadAllOut(Schema):
    count: int
    """How many changed. Zero means there was nothing to do."""

    unread: int
