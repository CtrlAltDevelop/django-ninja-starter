"""The desk, as a screen: one page where an agent answers people live.

The admin already has this app's *records* -- a ticket list, a thread as an
inline, categories and canned replies. What it has never had is the thing the
socket exists for: somebody typing an answer while the person who asked is still
there. A change form cannot be that, because it reloads to learn that a message
arrived, and a conversation that is a page refresh behind is a conversation the
client has already left.

So this is a second screen beside the change form rather than a replacement for
it. It is mounted under the ticket admin -- ``/admin/support/ticket/chat/`` --
so it inherits the admin's session, its permission checks and its navigation,
and it holds exactly one WebSocket for everything it does. Nothing on it posts a
form: the queue, the thread, sending, claiming, closing and the typing indicator
are all commands on :mod:`apps.support.sockets`, which means the desk screen and
any client widget speak the same protocol and cannot drift apart.

**The credential is the admin's own session cookie.** The socket already accepts
one -- see :mod:`apps.support.identity` -- so the page opens a connection
without minting a token, and an agent who signs out of the admin loses the
socket with everything else.

**One socket, and only this app's.** The admin's own live bell -- see
:mod:`infrastructure.common.adminlive` -- is on this page like any other, and it
already carries the notification feed. A second connection here would announce
every announcement twice, in two corners of the same screen.

**The page degrades rather than lies.** ``runserver`` is WSGI and serves no
WebSocket at all, and a deployment can publish this app over REST only. Both are
answered on the page itself, in a banner that says which one happened, because
the alternative is an agent staring at a queue that never loads and no reason
given.
"""

from typing import Any

from django.conf import settings
from django.core.exceptions import PermissionDenied
from django.http import HttpRequest, HttpResponse
from django.template.response import TemplateResponse
from django.urls import URLPattern, path, reverse

from apps.support.models import DESK_KINDS, CannedReply, Category, Kind, Priority, Status

try:  # pragma: no cover - exercised by whichever branch the project installs
    from unfold.admin import ModelAdmin  # noqa: F401

    ADMIN_BASE_TEMPLATE = "unfold/layouts/base_simple.html"
except ImportError:  # pragma: no cover - only in a project without Unfold
    ADMIN_BASE_TEMPLATE = "admin/base_site.html"

#: What an agent may do here. Reading the queue is the floor; everything the
#: page can *change* is refused by the socket itself for an account without the
#: rights, so this guards the door and the protocol guards each action.
VIEW_PERMISSION = "support.view_ticket"


def socket_available() -> bool:
    """Whether this deployment actually publishes the support WebSocket.

    A project can install the app and serve it over REST only, and one running
    ``runserver`` serves no socket whatever the setting says. The first is
    knowable here; the second is not, which is why the page says both.
    """
    return bool(settings.SUPPORT_ENABLED and "ws" in settings.SUPPORT_TRANSPORTS)


class LiveChatAdminMixin:
    """Adds the live desk screen to the ticket admin.

    A mixin rather than lines in :class:`~apps.support.admin.TicketAdmin`, so the
    screen is one import to drop and one import to remove, and so the admin file
    goes on being about columns and filters.
    """

    def get_urls(self) -> list[URLPattern]:
        """Put the desk screen ahead of the admin's catch-all object route.

        ``chat`` cannot be mistaken for a primary key -- this model's are UUIDs
        -- but the ordering is the rule for custom admin routes and staying with
        it costs nothing.
        """
        chat = path(
            "chat/",
            self.admin_site.admin_view(self.live_chat_view),  # type: ignore[attr-defined]
            name="support_live_chat",
        )
        return [chat, *super().get_urls()]  # type: ignore[misc]

    def live_chat_view(self, request: HttpRequest) -> HttpResponse:
        """Render the desk. Everything after this is the socket's work."""
        user = request.user
        if not (user.is_superuser or user.has_perm(VIEW_PERMISSION)):
            raise PermissionDenied
        return TemplateResponse(
            request,
            "admin/support/live_chat.html",
            self.live_chat_context(request),
        )

    def live_chat_context(self, request: HttpRequest) -> dict[str, Any]:
        """What the page needs before its first frame arrives.

        Vocabularies and canned replies are rendered rather than fetched: they
        change about once a quarter, and a screen that has to wait for a round
        trip to draw its own status dropdown is a screen that flickers on every
        open. Everything that changes by the minute -- threads, messages, who is
        typing -- comes down the socket and only down the socket.
        """
        placeholder = "00000000-0000-0000-0000-000000000000"
        return {
            **self.admin_site.each_context(request),  # type: ignore[attr-defined]
            "title": "Live chat",
            "subtitle": "Answer people while they are still here",
            "opts": self.opts,  # type: ignore[attr-defined]
            "base_template": ADMIN_BASE_TEMPLATE,
            "socket_available": socket_available(),
            "ws_path": settings.SUPPORT_WS_PATH,
            "statuses": Status.choices,
            "priorities": Priority.choices,
            # The desk's own two kinds. A group or a private chat between two
            # customers is not the desk's to answer, and listing them here would
            # invite an agent to walk into one -- which the socket would refuse
            # anyway, less politely.
            "kinds": [(value, label) for value, label in Kind.choices if value in DESK_KINDS],
            "categories": list(
                Category.objects.filter(is_active=True).values("slug", "name").order_by("name")
            ),
            "canned_replies": list(
                CannedReply.objects.filter(is_active=True).values("title", "body").order_by("title")
            ),
            "queue_url": reverse("admin:support_ticket_changelist"),
            # Everything the page's script reads, in one `json_script` block
            # rather than interpolated into the JavaScript: a username or a
            # subject rendered into a script tag is an escaping bug waiting to
            # be found by somebody who put a quote in their name.
            "config": {
                "socket_available": socket_available(),
                "ws_path": settings.SUPPORT_WS_PATH,
                "user_id": str(request.user.pk),
                "username": request.user.get_username(),
                "ticket_url_template": reverse("admin:support_ticket_change", args=(placeholder,)),
                "ticket_url_placeholder": placeholder,
            },
        }
