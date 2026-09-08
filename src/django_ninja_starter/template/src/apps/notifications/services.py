"""Reading notifications: the list, the badge, and everything an account can do to one.

The socket is the interesting half of this app, and this is the half that makes
it usable. A client that has just opened a page needs the history the socket
will never send it -- the socket delivers what happens next, plus a bounded
catch-up -- and a client that cannot hold a connection open at all needs a way
to work without one. Both are this.

Everything here is scoped to the caller. There is no "list notifications for
user X": the queryset starts from ``for_user`` and no argument can widen it, so
the only account a request can read is the one that made it.

Every state change also publishes to the caller's own channel, so an account
signed in on two devices sees a badge cleared on one clear on the other. That
happens here rather than in each transport, which is what keeps HTTP, GraphQL,
gRPC and the socket from drifting into three of them doing it and one not.

Writing a notification is not an API operation. Notifications are created by the
code that has something to say -- see :func:`apps.notifications.events.notify_user`
-- or by a staff member in the admin. An endpoint that let a client post a
notification to anybody would need an authorisation model this app does not have
and most projects do not want.
"""

from typing import Any
from uuid import UUID

from apps.notifications.events import payload, publish_state
from apps.notifications.models import (
    Notification,
    dismiss,
    dismiss_all,
    mark_all_read,
    mark_read,
    mark_unread,
    restore,
    unread_count,
)

MAX_PAGE = 200
DEFAULT_PAGE = 50


class NotificationNotFound(LookupError):
    """No such notification -- or one addressed to somebody else.

    The two are deliberately the same answer: saying which is which would
    confirm the existence of another account's mail.
    """


class NotificationService:
    """Everything an account can do to the notifications addressed to it."""

    # -- reading ----------------------------------------------------------

    def _page(
        self,
        user: Any,
        *,
        unread: bool | None,
        level: str | None,
        audience: str | None,
        include_dismissed: bool,
    ) -> Any:
        notifications = Notification.objects.for_user(user, include_dismissed=include_dismissed)
        if unread is True:
            notifications = notifications.unread()
        elif unread is False:
            notifications = notifications.read()
        if level:
            notifications = notifications.filter(level=level)
        if audience:
            notifications = notifications.filter(audience=audience)
        return notifications

    def list(
        self,
        user: Any,
        *,
        unread: bool | None = None,
        level: str | None = None,
        audience: str | None = None,
        include_dismissed: bool = False,
        limit: int = DEFAULT_PAGE,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        """Newest first: everything addressed to this account, and everything
        addressed to everybody, each row carrying what this account has done with it.

        ``unread=True`` narrows it to what is still outstanding, which is the
        query a notification tray actually makes on open. ``level`` and
        ``audience`` narrow it further -- an errors-only view, or announcements
        on their own. Dismissed rows are left out unless asked for, because
        dismissing is a request not to be shown something again.
        """
        page = max(1, min(limit, MAX_PAGE))
        start = max(0, offset)
        notifications = self._page(
            user,
            unread=unread,
            level=level,
            audience=audience,
            include_dismissed=include_dismissed,
        )
        return [
            payload(
                notification,
                read=notification.read_at is not None,
                dismissed=notification.dismissed_at is not None,
            )
            for notification in notifications[start : start + page]
        ]

    def count(
        self,
        user: Any,
        *,
        unread: bool | None = None,
        level: str | None = None,
        audience: str | None = None,
        include_dismissed: bool = False,
    ) -> int:
        """How many rows :meth:`list` would have to page through, ignoring the page.

        A client that renders "showing 50 of 312" would otherwise have to fetch
        all 312 to find out there were 312.
        """
        return self._page(
            user,
            unread=unread,
            level=level,
            audience=audience,
            include_dismissed=include_dismissed,
        ).count()

    def get(self, user: Any, notification_id: UUID) -> dict[str, Any]:
        """One notification this account can see, dismissed or not.

        Dismissed ones are included: a client following a link to something it
        cleared away should get the notification, not a 404 that reads as though
        somebody else's mail.
        """
        notification = self._resolve_with_state(user, notification_id)
        return payload(
            notification,
            read=notification.read_at is not None,
            dismissed=notification.dismissed_at is not None,
        )

    def unread_count(self, user: Any) -> int:
        """The badge number, without the payload of the list it counts."""
        return unread_count(user)

    # -- changing state ---------------------------------------------------

    def _resolve(self, user: Any, notification_id: UUID) -> Notification:
        notification = Notification.objects.visible_to(user).filter(pk=notification_id).first()
        if notification is None:
            raise NotificationNotFound("No such notification.")
        return notification

    def _resolve_with_state(self, user: Any, notification_id: UUID) -> Notification:
        notification = (
            Notification.objects.visible_to(user)
            .with_state(user)
            .filter(pk=notification_id)
            .first()
        )
        if notification is None:
            raise NotificationNotFound("No such notification.")
        return notification

    def _one(self, user: Any, notification_id: UUID, change: Any, action: str) -> dict[str, Any]:
        """Apply one state change to one notification and answer the same shape every time."""
        notification = self._resolve(user, notification_id)
        changed = change(user, notification)
        remaining = unread_count(user)
        if changed:
            publish_state(user, action, unread=remaining, ids=[notification.pk])
        return {"id": notification.pk, "unread": remaining, "changed": changed}

    def mark_read(self, user: Any, notification_id: UUID) -> dict[str, Any]:
        return self._one(user, notification_id, mark_read, "read")

    def mark_unread(self, user: Any, notification_id: UUID) -> dict[str, Any]:
        """Undo a read. Already-unread is success with ``changed`` false, not an error."""
        return self._one(user, notification_id, mark_unread, "unread")

    def dismiss(self, user: Any, notification_id: UUID) -> dict[str, Any]:
        """Take one out of this account's tray. Never deletes -- a broadcast is shared."""
        return self._one(user, notification_id, dismiss, "dismiss")

    def restore(self, user: Any, notification_id: UUID) -> dict[str, Any]:
        """Put a dismissed one back. Read state is left where it was."""
        return self._one(user, notification_id, restore, "restore")

    def _all(self, user: Any, change: Any, action: str) -> dict[str, int]:
        count = change(user)
        remaining = unread_count(user)
        if count:
            publish_state(user, action, unread=remaining)
        return {"count": count, "unread": remaining}

    def mark_all_read(self, user: Any) -> dict[str, int]:
        return self._all(user, mark_all_read, "read_all")

    def dismiss_all(self, user: Any) -> dict[str, int]:
        """Empty the tray. Everything it cleared is marked read on the way out."""
        return self._all(user, dismiss_all, "dismiss_all")


notification_service = NotificationService()
