"""Reading notifications: the list, the badge, and marking them read.

The socket is the interesting half of this app, and this is the half that makes
it usable. A client that has just opened a page needs the history the socket
will never send it -- the socket delivers what happens next, plus a bounded
catch-up -- and a client that cannot hold a connection open at all needs a way
to work without one. Both are this.

Everything here is scoped to the caller. There is no "list notifications for
user X": the queryset starts from ``for_user`` and no argument can widen it, so
the only account a request can read is the one that made it.

Writing a notification is not an API operation. Notifications are created by the
code that has something to say -- see :func:`apps.notifications.events.notify_user`
-- or by a staff member in the admin. An endpoint that let a client post a
notification to anybody would need an authorisation model this app does not have
and most projects do not want.
"""

from typing import Any
from uuid import UUID

from apps.notifications.events import payload
from apps.notifications.models import Notification, mark_all_read, mark_read, unread_count

MAX_PAGE = 200


class NotificationNotFound(LookupError):
    """No such notification -- or one addressed to somebody else.

    The two are deliberately the same answer: saying which is which would
    confirm the existence of another account's mail.
    """


class NotificationService:
    """Everything an account can do to the notifications addressed to it."""

    def list(
        self,
        user: Any,
        *,
        unread: bool | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        """Newest first: everything addressed to this account, and everything
        addressed to everybody, each row carrying whether this account has read it.

        ``unread=True`` narrows it to what is still outstanding, which is the
        query a notification tray actually makes on open.
        """
        page = max(1, min(limit, MAX_PAGE))
        start = max(0, offset)
        notifications = Notification.objects.for_user(user)
        if unread is True:
            notifications = notifications.unread()
        elif unread is False:
            notifications = notifications.filter(read_at__isnull=False)
        return [
            payload(notification, read=notification.read_at is not None)
            for notification in notifications[start : start + page]
        ]

    def unread_count(self, user: Any) -> int:
        """The badge number, without the payload of the list it counts."""
        return unread_count(user)

    def mark_all_read(self, user: Any) -> dict[str, int]:
        return {"count": mark_all_read(user), "unread": 0}

    def mark_read(self, user: Any, notification_id: UUID) -> dict[str, Any]:
        notification = Notification.objects.visible_to(user).filter(pk=notification_id).first()
        if notification is None:
            raise NotificationNotFound("No such notification.")
        mark_read(user, notification)
        return {"id": notification.pk, "unread": unread_count(user)}


notification_service = NotificationService()
