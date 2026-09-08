"""Fixtures for the notification tests: two accounts, some traffic, and a socket client.

The app is optional, so its tests are too. A project that has not enabled it has
no notification tables and no registered models, and importing one raises before
pytest can say anything useful -- so collection stops here instead, and the rest
of that project's suite runs as normal.
"""

import asyncio
import json
from collections.abc import Iterator
from typing import Any

import pytest
from django.apps import apps as django_apps
from django.conf import settings
from django.contrib.auth import get_user_model
from django.test import RequestFactory

NOTIFICATIONS_INSTALLED = django_apps.is_installed("apps.notifications")
collect_ignore_glob = [] if NOTIFICATIONS_INSTALLED else ["*"]

if NOTIFICATIONS_INSTALLED:
    from apps.notifications.broadcast import reset_broker
    from apps.notifications.models import Audience, Notification


@pytest.fixture(autouse=True)
def _fresh_broker() -> Iterator[None]:
    """The broker lives for the life of the process, so subscriptions would leak."""
    reset_broker()
    yield
    reset_broker()


@pytest.fixture
def alice(db: None) -> Any:
    return get_user_model().objects.create_user(username="alice", email="alice@example.test")


@pytest.fixture
def bob(db: None) -> Any:
    return get_user_model().objects.create_user(username="bob", email="bob@example.test")


@pytest.fixture
def announcement(db: None) -> Notification:
    """Addressed to everybody, including whoever is not signed in."""
    return Notification.objects.create(
        audience=Audience.GLOBAL, subject="Scheduled maintenance", body="Sunday, 02:00 UTC."
    )


@pytest.fixture
def for_alice(alice: Any) -> Notification:
    return Notification.objects.create(
        audience=Audience.USER, recipient=alice, subject="Your export is ready", link="/exports/1"
    )


@pytest.fixture
def for_bob(bob: Any) -> Notification:
    return Notification.objects.create(
        audience=Audience.USER, recipient=bob, subject="Somebody mentioned you"
    )


def access_token(user: Any) -> str:
    """A real credential from the project's own issuer, not a hand-rolled JWT.

    Minting it the way a login does is the point: a token the socket accepts but
    the API would not, or the reverse, is exactly the bug worth catching.
    """
    from infrastructure.auth.core.sessions import issue_credentials

    request = RequestFactory().post("/")
    return issue_credentials(request, user, method="password").access_token


class SocketClient:
    """Drives an ASGI application over the two queues an ASGI server would provide.

    No server and no network: the consumer is called directly, which makes these
    tests as fast as the rest of the suite and lets them assert on the exact
    frames rather than on whatever a client library decided to do with them.
    """

    def __init__(
        self,
        application: Any,
        *,
        path: str | None = None,
        query: str = "",
        headers: list[tuple[bytes, bytes]] | None = None,
    ) -> None:
        self._application = application
        self._scope = {
            "type": "websocket",
            "path": path if path is not None else settings.NOTIFICATIONS_WS_PATH,
            "query_string": query.encode(),
            "headers": headers or [],
        }
        self._to_server: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        self._to_client: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        self._task: asyncio.Task[None] | None = None

    async def _receive(self) -> dict[str, Any]:
        return await self._to_server.get()

    async def _send(self, message: dict[str, Any]) -> None:
        await self._to_client.put(message)

    async def open(self) -> dict[str, Any]:
        """Perform the handshake and return whatever the server answered it with."""
        self._task = asyncio.ensure_future(
            self._application(self._scope, self._receive, self._send)
        )
        await self._to_server.put({"type": "websocket.connect"})
        return await self.next_message()

    async def next_message(self, timeout: float = 5.0) -> dict[str, Any]:
        return await asyncio.wait_for(self._to_client.get(), timeout)

    async def next_frame(self, timeout: float = 5.0) -> dict[str, Any]:
        message = await self.next_message(timeout)
        assert message["type"] == "websocket.send", message
        return json.loads(message["text"])

    async def send(self, frame: dict[str, Any]) -> None:
        await self._to_server.put({"type": "websocket.receive", "text": json.dumps(frame)})

    async def send_text(self, text: str) -> None:
        await self._to_server.put({"type": "websocket.receive", "text": text})

    async def command(self, frame: dict[str, Any]) -> dict[str, Any]:
        """Send a command and read the one frame it answers with."""
        await self.send(frame)
        return await self.next_frame()

    async def close(self) -> None:
        await self._to_server.put({"type": "websocket.disconnect", "code": 1000})
        if self._task is not None:
            await asyncio.wait_for(self._task, timeout=5.0)
