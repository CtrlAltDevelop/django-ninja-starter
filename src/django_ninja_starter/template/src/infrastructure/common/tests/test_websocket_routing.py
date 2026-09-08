"""The project's WebSocket routing table: what is mounted, and what happens otherwise.

Lives here rather than with the notifications app because the routing is the
project's decision, the same way ``config/urls.py`` is -- an app publishes a
socket application, and this is where the project says whether it is served.
"""

import asyncio
from typing import Any

from django.conf import settings
from django.test import override_settings

from config.sockets import NO_SUCH_ROUTE, websocket_application, websocket_routes


async def _handshake(path: str) -> list[dict[str, Any]]:
    """Open a connection at ``path`` and collect what the server says back."""
    incoming: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
    sent: list[dict[str, Any]] = []
    await incoming.put({"type": "websocket.connect"})

    async def receive() -> dict[str, Any]:
        return await incoming.get()

    async def send(message: dict[str, Any]) -> None:
        sent.append(message)
        if message["type"] == "websocket.accept":
            await incoming.put({"type": "websocket.disconnect", "code": 1000})

    scope = {"type": "websocket", "path": path, "query_string": b"", "headers": []}
    await websocket_application(scope, receive, send)
    return sent


def test_an_unrouted_path_is_refused_rather_than_left_hanging() -> None:
    """Closing without reading the handshake is how a connection hangs instead."""
    sent = asyncio.run(_handshake("/ws/nothing-here"))

    assert sent == [{"type": "websocket.close", "code": NO_SUCH_ROUTE}]


def test_the_notification_socket_is_mounted_when_the_app_is_enabled() -> None:
    assert settings.NOTIFICATIONS_ENABLED
    assert websocket_routes() == [
        (settings.NOTIFICATIONS_WS_PATH, "apps.notifications.sockets.notifications_socket")
    ]


def test_a_project_without_notifications_serves_no_socket_at_all() -> None:
    with override_settings(NOTIFICATIONS_ENABLED=False):
        assert websocket_routes() == []
        assert asyncio.run(_handshake(settings.NOTIFICATIONS_WS_PATH)) == [
            {"type": "websocket.close", "code": NO_SUCH_ROUTE}
        ]


def test_a_trailing_slash_reaches_the_same_socket(db: None) -> None:
    """Nobody should have to know whether the path they were given ended in one."""
    sent = asyncio.run(_handshake(f"{settings.NOTIFICATIONS_WS_PATH}/"))

    assert sent[0]["type"] == "websocket.accept"
