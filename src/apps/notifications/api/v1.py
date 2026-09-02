"""Reading notifications over HTTP: the list, the badge, and marking them read.

The socket is the interesting half of this app, and this is the half that makes
it usable. A client that has just opened a page needs the history the socket
will never send it -- the socket delivers what happens next, plus a bounded
catch-up -- and a client that cannot hold a connection open at all needs a way
to work without one. Both are this.

Everything here is scoped to the caller. There is no "list notifications for
user X": the queryset starts from ``for_user`` and no parameter can widen it, so
the only account a request can read is the one that made it.

Writing a notification is not an API operation. Notifications are created by the
code that has something to say -- see :func:`apps.notifications.events.notify_user`
-- or by a staff member in the admin. An endpoint that let a client post a
notification to anybody would need an authorisation model this app does not have
and most projects do not want.
"""

from typing import Any
from uuid import UUID

from django.http import HttpRequest
from ninja import Router
from ninja.errors import HttpError

from apps.notifications.events import payload
from apps.notifications.models import Notification, mark_all_read, mark_read, unread_count
from apps.notifications.schemas import NotificationOut, ReadAllOut, ReadOut, UnreadCountOut

try:  # pragma: no cover - exercised by whichever branch the project installs
    from infrastructure.auth.core.sessions import api_auth
except ImportError:  # pragma: no cover - only in a project without the auth apps
    from ninja.security import django_auth as api_auth  # type: ignore[assignment]

MAX_PAGE = 200

router = Router(auth=api_auth)


@router.get(
    "",
    response=list[NotificationOut],
    summary="List every notification this account can see",
)
def list_notifications(
    request: HttpRequest,
    unread: bool | None = None,
    limit: int = 50,
    offset: int = 0,
) -> list[dict[str, Any]]:
    """Newest first: everything addressed to this account, and everything addressed
    to everybody, each row carrying whether this account has read it.

    ``?unread=true`` narrows it to what is still outstanding, which is the query
    a notification tray actually makes on open.
    """
    page = max(1, min(limit, MAX_PAGE))
    start = max(0, offset)
    notifications = Notification.objects.for_user(request.user)
    if unread is True:
        notifications = notifications.unread()
    elif unread is False:
        notifications = notifications.filter(read_at__isnull=False)
    return [
        payload(notification, read=notification.read_at is not None)
        for notification in notifications[start : start + page]
    ]


@router.get("/unread-count", response=UnreadCountOut, summary="Count what is still unread")
def count_unread(request: HttpRequest) -> dict[str, int]:
    """The badge number, without the payload of the list it counts."""
    return {"count": unread_count(request.user)}


@router.post("/read-all", response=ReadAllOut, summary="Mark everything read")
def read_everything(request: HttpRequest) -> dict[str, int]:
    return {"count": mark_all_read(request.user), "unread": 0}


@router.post("/{notification_id}/read", response=ReadOut, summary="Mark one notification read")
def read_one(request: HttpRequest, notification_id: UUID) -> dict[str, Any]:
    """A 404 both for a notification that does not exist and for one addressed to
    somebody else -- the two are the same answer, and saying which is which would
    confirm the existence of another account's mail."""
    notification = Notification.objects.visible_to(request.user).filter(pk=notification_id).first()
    if notification is None:
        raise HttpError(404, "No such notification.")
    mark_read(request.user, notification)
    return {"id": notification.pk, "unread": unread_count(request.user)}
