"""Turning a saved row into something the sockets can be handed.

Broadcasting is wired to ``post_save`` rather than done at each call site, for
the same reason the response envelope is applied by the renderer rather than by
each view: there is no path that creates a notification and forgets to send it.
The admin, a management command, a shell session and :func:`notify_user` all
reach the sockets by the one route.

It is deferred to ``on_commit``, so nothing is pushed to a client for a row that
a later exception rolls back. A client told about a notification it can never
fetch is worse than a client told a moment later.

The wire format is a frame with a ``type``, not a bare notification. A socket
that only ever receives one shape of message forces every future addition to be
smuggled inside a field of the payload -- and there is already a second kind:
:func:`publish_state`, which tells an account's *other* connections that
something was read or dismissed, so a badge cleared on a phone clears on the
laptop too.
"""

from collections.abc import Iterable
from typing import Any

from django.db import transaction
from django.db.models.signals import post_save
from django.dispatch import receiver

from apps.notifications.broadcast import get_broker, global_channel, user_channel
from apps.notifications.models import Audience, Level, Notification

NOTIFICATION_FRAME = "notification"
STATE_FRAME = "state"


def payload(
    notification: Notification, *, read: bool = False, dismissed: bool = False
) -> dict[str, Any]:
    """One notification as a client receives it, over the socket or over HTTP.

    The same function serves both, because a client that had to reconcile two
    shapes of the same object would write the reconciliation itself.
    """
    return {
        "id": str(notification.pk),
        "audience": notification.audience,
        "subject": notification.subject,
        "body": notification.body,
        "level": notification.level,
        "link": notification.link,
        "data": notification.data,
        "created_at": notification.created_at.isoformat(),
        "read": read,
        "dismissed": dismissed,
    }


def channel_for(notification: Notification) -> str:
    """Where this notification goes: everybody's channel, or one account's."""
    if notification.is_global:
        return global_channel()
    return user_channel(notification.recipient_id)


def publish(notification: Notification) -> None:
    """Push a notification to whichever channel its audience listens on, after commit."""
    frame = {"type": NOTIFICATION_FRAME, "notification": payload(notification)}
    _after_commit(channel_for(notification), frame)


def publish_state(user: Any, action: str, *, unread: int, ids: Iterable[Any] = ()) -> None:
    """Tell one account's other connections that its read state moved.

    Sent to the account's own channel, which every one of its connections is on
    -- including the one that asked for the change. That connection gets its
    direct reply *and* this frame, which is redundant rather than wrong: both
    say the same thing, applying either twice is a no-op, and the alternative is
    a per-connection exclusion the broker has no way to express.
    """
    frame = {
        "type": STATE_FRAME,
        "action": action,
        "ids": [str(identifier) for identifier in ids],
        "unread": unread,
    }
    _after_commit(user_channel(user.pk), frame)


def _after_commit(channel: str, frame: dict[str, Any]) -> None:
    broker = get_broker()
    transaction.on_commit(lambda: broker.publish(channel, frame))


@receiver(post_save, sender=Notification, dispatch_uid="notifications.broadcast")
def _broadcast_new_notification(
    sender: type[Notification], instance: Notification, created: bool, **kwargs: object
) -> None:
    """Only on creation: editing a typo in the admin should not re-alert anybody."""
    if created:
        publish(instance)


def connect() -> None:
    """Called from ``AppConfig.ready``. Importing this module is what registers the
    receiver; this exists so that the import is deliberate rather than incidental."""


def notify_user(
    user: Any,
    subject: str,
    *,
    body: str = "",
    level: str = "",
    link: str = "",
    data: dict[str, Any] | None = None,
) -> Notification:
    """Tell one account something. The socket delivery is the signal's job."""
    return Notification.objects.create(
        audience=Audience.USER,
        recipient=user,
        subject=subject,
        body=body,
        level=level or Level.INFO,
        link=link,
        data=data or {},
    )


def notify_users(
    users: Iterable[Any],
    subject: str,
    *,
    body: str = "",
    level: str = "",
    link: str = "",
    data: dict[str, Any] | None = None,
) -> list[Notification]:
    """Tell several accounts the same thing, one notification each.

    One row per recipient rather than one shared row, because read state is per
    account and a shared row would have to invent a roster of who it was for.

    Deliberately *not* ``bulk_create``: that skips ``post_save``, so every one of
    these would be saved and none of them delivered -- the exact failure the
    signal exists to make impossible. The loop is the price of that guarantee,
    and a send to a list long enough for it to hurt belongs in a task queue
    anyway. Duplicate accounts in ``users`` are collapsed, so a caller building
    the list from two overlapping queries does not send twice.
    """
    seen: set[Any] = set()
    created: list[Notification] = []
    for user in users:
        if user.pk in seen:
            continue
        seen.add(user.pk)
        created.append(notify_user(user, subject, body=body, level=level, link=link, data=data))
    return created


def notify_everyone(
    subject: str,
    *,
    body: str = "",
    level: str = "",
    link: str = "",
    data: dict[str, Any] | None = None,
) -> Notification:
    """Tell everybody something, including whoever connects anonymously."""
    return Notification.objects.create(
        audience=Audience.GLOBAL,
        recipient=None,
        subject=subject,
        body=body,
        level=level or Level.INFO,
        link=link,
        data=data or {},
    )
