"""Reading notifications over HTTP: the list, the badge, and marking them read.

Every decision is :class:`NotificationService`'s -- including the one that
matters, which is that the queryset starts from the caller and no parameter can
widen it.
"""

from typing import Any
from uuid import UUID

from django.http import HttpRequest
from ninja import Router
from ninja.errors import HttpError

from apps.notifications.rest.schemas import NotificationOut, ReadAllOut, ReadOut, UnreadCountOut
from apps.notifications.services import NotificationNotFound, notification_service

try:  # pragma: no cover - exercised by whichever branch the project installs
    from infrastructure.auth.core.sessions import api_auth
except ImportError:  # pragma: no cover - only in a project without the auth apps
    from ninja.security import django_auth as api_auth  # type: ignore[assignment]

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
    """Newest first, each row carrying whether this account has read it.

    ``?unread=true`` narrows it to what is still outstanding, which is the query
    a notification tray actually makes on open.
    """
    return notification_service.list(request.user, unread=unread, limit=limit, offset=offset)


@router.get("/unread-count", response=UnreadCountOut, summary="Count what is still unread")
def count_unread(request: HttpRequest) -> dict[str, int]:
    """The badge number, without the payload of the list it counts."""
    return {"count": notification_service.unread_count(request.user)}


@router.post("/read-all", response=ReadAllOut, summary="Mark everything read")
def read_everything(request: HttpRequest) -> dict[str, int]:
    return notification_service.mark_all_read(request.user)


@router.post("/{notification_id}/read", response=ReadOut, summary="Mark one notification read")
def read_one(request: HttpRequest, notification_id: UUID) -> dict[str, Any]:
    """A 404 both for a notification that does not exist and for one addressed to
    somebody else -- the two are the same answer."""
    try:
        return notification_service.mark_read(request.user, notification_id)
    except NotificationNotFound as missing:
        raise HttpError(404, str(missing)) from None
