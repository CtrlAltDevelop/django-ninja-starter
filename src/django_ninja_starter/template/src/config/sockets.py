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

from django.conf import settings
from django.utils.module_loading import import_string

type AsgiApplication = Any

# The close code for "there is nothing here". 4404 rather than 1000, because a
# client that connected to the wrong path should be able to tell that from a
# server that hung up on it, and the 4000 range is the one reserved for
# application-defined meanings.
NO_SUCH_ROUTE = 4404


def websocket_routes() -> list[tuple[str, str]]:
    """``(path, dotted path to an ASGI application)`` for each mounted socket."""
    routes = []
    if settings.NOTIFICATIONS_ENABLED:
        routes.append(
            (settings.NOTIFICATIONS_WS_PATH, "apps.notifications.sockets.notifications_socket")
        )
    if settings.SUPPORT_ENABLED:
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
    await import_string(application)(scope, receive, send)
