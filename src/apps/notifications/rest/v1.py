"""Reading notifications over HTTP: the history, the badge, and the tray.

Every decision is :class:`NotificationService`'s -- including the one that
matters, which is that the queryset starts from the caller and no parameter can
widen it.

The verbs mirror the socket's commands one for one, under the same names and
with the same replies, so a client can move between the two without a second
mental model. Nothing here creates a notification: the code with something to
say does that.
"""

from typing import Any
from uuid import UUID

from django.http import HttpRequest
from ninja import Router
from ninja.errors import HttpError

from apps.notifications.models import Audience, Level
from apps.notifications.rest.schemas import (
    NotificationOut,
    NotificationPage,
    ReadAllOut,
    ReadOut,
    UnreadCountOut,
)
from apps.notifications.services import DEFAULT_PAGE, NotificationNotFound, notification_service

try:  # pragma: no cover - exercised by whichever branch the project installs
    from infrastructure.auth.core.sessions import api_auth
except ImportError:  # pragma: no cover - only in a project without the auth apps
    from ninja.security import django_auth as api_auth  # type: ignore[assignment]

router = Router(auth=api_auth)


def _change(request: HttpRequest, notification_id: UUID, method: str) -> dict[str, Any]:
    """Run one of the service's single-notification changes, translating its refusal.

    A 404 both for a notification that does not exist and for one addressed to
    somebody else -- the two are the same answer, since saying which would
    confirm the existence of another account's mail.
    """
    try:
        return getattr(notification_service, method)(request.user, notification_id)
    except NotificationNotFound as missing:
        raise HttpError(404, str(missing)) from None


@router.get(
    "",
    response=NotificationPage,
    summary="List every notification this account can see",
)
def list_notifications(
    request: HttpRequest,
    unread: bool | None = None,
    level: Level | None = None,
    audience: Audience | None = None,
    include_dismissed: bool = False,
    limit: int = DEFAULT_PAGE,
    offset: int = 0,
) -> dict[str, Any]:
    """Newest first, each row carrying what this account has done with it.

    `?unread=true` narrows it to what is still outstanding, which is the query a
    notification tray actually makes on open; `?level=` and `?audience=` narrow
    it further. Dismissed rows are left out unless `?include_dismissed=true`,
    because dismissing is a request not to be shown something again.

    `total` counts everything matching the same filters, so a client can page
    without a second call.
    """
    filters: dict[str, Any] = {
        "unread": unread,
        "level": level,
        "audience": audience,
        "include_dismissed": include_dismissed,
    }
    return {
        "notifications": notification_service.list(
            request.user, limit=limit, offset=offset, **filters
        ),
        "total": notification_service.count(request.user, **filters),
        "limit": limit,
        "offset": offset,
    }


@router.get("/unread-count", response=UnreadCountOut, summary="Count what is still unread")
def count_unread(request: HttpRequest) -> dict[str, int]:
    """The badge number, without the payload of the list it counts."""
    return {"count": notification_service.unread_count(request.user)}


@router.post("/read-all", response=ReadAllOut, summary="Mark everything read")
def read_everything(request: HttpRequest) -> dict[str, int]:
    return notification_service.mark_all_read(request.user)


@router.post("/dismiss-all", response=ReadAllOut, summary="Empty the tray")
def dismiss_everything(request: HttpRequest) -> dict[str, int]:
    """Clear every notification out of this account's tray, marking them read.

    Never a delete: a broadcast belongs to everybody, and one person clearing an
    announcement must not remove it from anyone else's tray.
    """
    return notification_service.dismiss_all(request.user)


@router.get("/{notification_id}", response=NotificationOut, summary="Read one notification")
def get_one(request: HttpRequest, notification_id: UUID) -> dict[str, Any]:
    """One notification, dismissed or not -- a link to something cleared away
    should still open it."""
    try:
        return notification_service.get(request.user, notification_id)
    except NotificationNotFound as missing:
        raise HttpError(404, str(missing)) from None


@router.post("/{notification_id}/read", response=ReadOut, summary="Mark one notification read")
def read_one(request: HttpRequest, notification_id: UUID) -> dict[str, Any]:
    return _change(request, notification_id, "mark_read")


@router.post("/{notification_id}/unread", response=ReadOut, summary="Mark one notification unread")
def unread_one(request: HttpRequest, notification_id: UUID) -> dict[str, Any]:
    """Undo a read. Already unread is success with `changed: false`, not an error."""
    return _change(request, notification_id, "mark_unread")


@router.post("/{notification_id}/dismiss", response=ReadOut, summary="Take one out of the tray")
def dismiss_one(request: HttpRequest, notification_id: UUID) -> dict[str, Any]:
    """Dismiss one notification for this account only, marking it read on the way."""
    return _change(request, notification_id, "dismiss")


@router.post("/{notification_id}/restore", response=ReadOut, summary="Put a dismissed one back")
def restore_one(request: HttpRequest, notification_id: UUID) -> dict[str, Any]:
    """Undo a dismissal. Read state is left where it was."""
    return _change(request, notification_id, "restore")
