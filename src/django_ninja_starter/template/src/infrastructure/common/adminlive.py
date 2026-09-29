"""The live corner of the admin: one script, served with the paths baked in.

Two of this project's apps push over WebSockets -- notifications and support --
and both of them are things somebody working in the admin wants to hear about
while they are on some other page. A badge that only appears on the screen that
owns it is a badge nobody sees: an agent editing a product has no reason to be
looking at the desk, which is exactly when a chat arrives.

So the feed is admin *chrome* rather than either app's screen, and it is mounted
here, by the project, for the same reason ``config/sockets.py`` mounts the
sockets themselves: which apps are installed is the project's question, not an
app's.

**It is a view rather than a static file.** The script has to know two paths and
whether either socket is published at all, and those are settings -- a static
file would have to be told at runtime anyway, by an endpoint or a data
attribute, and neither exists on a page whose ``<head>`` this is injected into
by the theme. Rendering the script itself is one round trip instead of two, and
it is cached like any other response.

It is registered through ``UNFOLD["SCRIPTS"]``, which Unfold resolves per
request and renders into every admin page. A project without Unfold can include
``common/admin_live.js`` from its own base template instead; nothing in the
script knows about the theme.
"""

from typing import Any

from django.conf import settings
from django.http import HttpRequest, HttpResponse
from django.template.response import TemplateResponse
from django.urls import NoReverseMatch, reverse


def _path_for(app: str, default_setting: str) -> str:
    """The WebSocket path one app publishes, or ``""`` if it publishes none.

    Read off settings with ``getattr`` rather than imported: every feature app
    here is optional, and a deployment that dropped one should serve an admin
    that is quieter, not one that fails to start.
    """
    if not getattr(settings, f"{app}_ENABLED", False):
        return ""
    if "ws" not in getattr(settings, f"{app}_TRANSPORTS", ()):
        return ""
    return str(getattr(settings, default_setting, "") or "")


def live_feeds() -> dict[str, str]:
    """The sockets this deployment actually publishes, by name."""
    return {
        "notifications": _path_for("NOTIFICATIONS", "NOTIFICATIONS_WS_PATH"),
        "support": _path_for("SUPPORT", "SUPPORT_WS_PATH"),
    }


def anything_live() -> bool:
    """Whether there is any feed at all, so the project can skip the script."""
    return any(live_feeds().values())


def _admin_url(name: str) -> str:
    """An admin route's address, or ``""`` where this project does not mount it.

    Asked for rather than assumed: a project can install either app and register
    its own admin without the screen this links to, and a bell that raised
    ``NoReverseMatch`` would take every admin page with it.
    """
    try:
        return reverse(name)
    except NoReverseMatch:  # pragma: no cover - only where an admin was replaced
        return ""


def live_script(request: HttpRequest) -> HttpResponse:
    """The script the admin loads on every page.

    Served to anybody the admin serves, staff or not: it carries two paths and
    no data, and the sockets themselves decide what an account may hear -- the
    support one refuses a handshake it cannot name, and the notification one
    delivers only what was addressed to everybody until it is told who is
    asking.
    """
    feeds = live_feeds()
    context: dict[str, Any] = {
        "notifications_ws_path": feeds["notifications"],
        "support_ws_path": feeds["support"],
        # Where the bell goes when it is pressed, which is wherever the thing
        # it is counting actually is: the desk for a waiting conversation, the
        # notification list otherwise. A bell that only clears itself is a
        # button that does nothing twice in a row.
        "chat_url": _admin_url("admin:support_live_chat") if feeds["support"] else "",
        "notifications_url": (
            _admin_url("admin:notifications_notification_changelist")
            if feeds["notifications"]
            else ""
        ),
    }
    return TemplateResponse(
        request,
        "common/admin_live.js",
        context,
        content_type="text/javascript",
    )


def live_script_url(request: HttpRequest) -> str:
    """The ``UNFOLD["SCRIPTS"]`` entry. Unfold calls this once per admin page."""
    return reverse("admin-live-script")
