"""Where a WebSocket connection is routed, and what happens when it matches nothing.

The HTTP side of this project has ``config/urls.py``; this is the same idea for
the other protocol, and it is deliberately the project's file rather than an
app's. An app publishes a socket application the way it publishes a router, and
the project decides whether it is mounted and at what path -- so a project that
has not enabled notifications serves no socket, exactly as it serves no
``/notifications`` routes -- and the same is true of support.

Routes are resolved per connection rather than at import, because that is what
lets ``override_settings`` move the path in a test and lets an app be enabled
without this module knowing anything about it beyond a setting.
"""

from typing import Any
from urllib.parse import urlparse

from django.conf import settings
from django.utils.module_loading import import_string

type AsgiApplication = Any

# The close code for "there is nothing here". 4404 rather than 1000, because a
# client that connected to the wrong path should be able to tell that from a
# server that hung up on it, and the 4000 range is the one reserved for
# application-defined meanings.
NO_SUCH_ROUTE = 4404

# The close code for "not from a page this deployment serves". Its own code
# rather than sharing 4404, because the two are different problems: a client on
# the wrong path fixes its URL, and a handshake refused for its origin means
# somebody's browser was told to open this socket by a page that is not ours.
BAD_ORIGIN = 4403


def websocket_routes() -> list[tuple[str, str]]:
    """``(path, dotted path to an ASGI application)`` for each mounted socket."""
    routes = []
    if settings.NOTIFICATIONS_ENABLED and "ws" in settings.NOTIFICATIONS_TRANSPORTS:
        routes.append(
            (settings.NOTIFICATIONS_WS_PATH, "apps.notifications.sockets.notifications_socket")
        )
    if settings.SUPPORT_ENABLED and "ws" in settings.SUPPORT_TRANSPORTS:
        routes.append((settings.SUPPORT_WS_PATH, "apps.support.sockets.support_socket"))
    return routes


def _match(path: str) -> str | None:
    wanted = path.rstrip("/")
    for route, application in websocket_routes():
        if wanted == route.rstrip("/"):
            return application
    return None


async def websocket_application(scope: dict[str, Any], receive: Any, send: Any) -> None:
    """Dispatch to whichever app owns this path, or refuse the handshake.

    An unmatched path still has to consume ``websocket.connect`` before closing:
    ASGI servers expect the handshake to be answered, and closing without reading
    it is how a connection ends up hanging rather than being refused.
    """
    application = _match(scope.get("path", ""))
    if application is None:
        await receive()
        await send({"type": "websocket.close", "code": NO_SUCH_ROUTE})
        return
    if not origin_allowed(scope):
        await receive()
        await send({"type": "websocket.close", "code": BAD_ORIGIN})
        return
    await import_string(application)(scope, receive, send)


def allowed_origins() -> list[str]:
    """The hosts a browser may open one of these sockets from.

    ``DJANGO_WEBSOCKET_ALLOWED_ORIGINS`` when a deployment names them, and
    ``ALLOWED_HOSTS`` when it does not -- because the pages that legitimately
    open these sockets are, almost always, the pages this deployment serves.
    A project whose frontend is on another domain names that domain here, and
    naming ``*`` turns the check off for a deployment that has decided its
    sockets are genuinely public.
    """
    configured = getattr(settings, "WEBSOCKET_ALLOWED_ORIGINS", None)
    if configured:
        return [str(item) for item in configured]
    return [str(host) for host in settings.ALLOWED_HOSTS]


def origin_allowed(scope: dict[str, Any]) -> bool:
    """Whether this handshake came from somewhere allowed to open the socket.

    **Browsers do not apply the same-origin policy to WebSockets.** Any page can
    ask for a socket to any host, and the browser sends the handshake -- with
    cookies. The identity these sockets accept includes a session cookie (see
    ``apps.notifications.identity``), so without this check a deployment whose
    cookie reaches a cross-site handshake is one where any page a signed-in
    person visits can open their notification feed and read it. That is
    cross-site WebSocket hijacking, and the handshake is the only place to stop
    it.

    A modern browser's ``SameSite=Lax`` default already withholds the cookie
    here, so this is the second lock rather than the first -- which matters
    precisely because the deployments that need it most are the ones that had to
    set ``SameSite=None`` to put their frontend on another domain.

    A handshake with no ``Origin`` at all is allowed: that is a native client, a
    server-to-server consumer or a test, none of which a browser's ambient
    credentials are reachable from. The header cannot be forged by a page --
    browsers set it themselves -- which is what makes checking it worth anything.
    """
    origin = ""
    for key, value in scope.get("headers") or []:
        if key == b"origin":
            origin = value.decode("latin-1")
            break
    if not origin:
        return True
    allowed = allowed_origins()
    if "*" in allowed:
        return True
    host = urlparse(origin).hostname or ""
    return any(host == entry or entry == "*" for entry in allowed)
