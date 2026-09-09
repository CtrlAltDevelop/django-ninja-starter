"""Fixtures for the support tests: a client, two agents, a category, and a socket client.

The app is optional, so its tests are too. A project that has not enabled it has
no support tables and no registered models, and importing one raises before
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

SUPPORT_INSTALLED = django_apps.is_installed("apps.support")
collect_ignore_glob = [] if SUPPORT_INSTALLED else ["*"]

if SUPPORT_INSTALLED:
    from apps.support.broadcast import reset_broker
    from apps.support.models import CannedReply, Category, Tag, Ticket, create_ticket, post_message


@pytest.fixture(autouse=True)
def _temporary_media(tmp_path: Any) -> Iterator[None]:
    """Every test in this app writes its attachments into a directory of its own.

    Autouse rather than opt-in: an upload reaches storage from the service, the
    routes and the socket, so a test that stages a file does not always look
    like one, and the cost of getting it wrong is files left in the repository
    after a suite run.
    """
    from django.test import override_settings

    with override_settings(MEDIA_ROOT=tmp_path, MEDIA_URL="/media/"):
        yield


@pytest.fixture(autouse=True)
def _fresh_broker() -> Iterator[None]:
    """The broker lives for the life of the process, so subscriptions would leak."""
    reset_broker()
    yield
    reset_broker()


@pytest.fixture
def client_user(db: None) -> Any:
    """Somebody with a problem. Not staff, which is the whole distinction here."""
    return get_user_model().objects.create_user(username="clara", email="clara@example.test")


@pytest.fixture
def other_client(db: None) -> Any:
    """A second client, for proving that one cannot see the other's conversations."""
    return get_user_model().objects.create_user(username="colin", email="colin@example.test")


@pytest.fixture
def agent(db: None) -> Any:
    return get_user_model().objects.create_user(
        username="agatha", email="agatha@example.test", is_staff=True
    )


@pytest.fixture
def second_agent(db: None) -> Any:
    return get_user_model().objects.create_user(
        username="alan", email="alan@example.test", is_staff=True
    )


@pytest.fixture
def category(db: None) -> "Category":
    """A category that promises something, so the SLA has numbers to work with."""
    return Category.objects.create(
        name="Billing",
        description="Invoices, payments and refunds.",
        first_response_minutes=60,
        resolution_minutes=60 * 24,
    )


@pytest.fixture
def slow_category(db: None) -> "Category":
    """A category that promises nothing, so "no promise" has a case too."""
    return Category.objects.create(name="General")


@pytest.fixture
def tag(db: None) -> "Tag":
    return Tag.objects.create(name="Escalated", colour="#dc2626")


@pytest.fixture
def canned(db: None) -> "CannedReply":
    return CannedReply.objects.create(
        title="Asking for an invoice number", body="Could you send us the invoice number?"
    )


@pytest.fixture
def ticket(client_user: Any, category: "Category") -> "Ticket":
    """One open ticket with one message in it, from the client."""
    created = create_ticket(client_user, subject="I was charged twice", category=category)
    post_message(created, client_user, "There are two charges on the 3rd.")
    created.refresh_from_db()
    return created


@pytest.fixture
def chat(client_user: Any) -> "Ticket":
    """A live chat: no subject, no category, no promise."""
    from apps.support.models import Kind

    return create_ticket(client_user, kind=str(Kind.CHAT))


def access_token(user: Any) -> str:
    """A real credential from the project's own issuer, not a hand-rolled JWT.

    Minting it the way a login does is the point: a token the socket accepts but
    the API would not, or the reverse, is exactly the bug worth catching.
    """
    from infrastructure.auth.core.sessions import issue_credentials

    request = RequestFactory().post("/")
    return issue_credentials(request, user, method="password").access_token


def auth(user: Any) -> dict[str, str]:
    """The header an HTTP test sends."""
    return {"HTTP_AUTHORIZATION": f"Bearer {access_token(user)}"}


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
            "path": path if path is not None else settings.SUPPORT_WS_PATH,
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

    async def frame_of_type(self, wanted: str, timeout: float = 5.0) -> dict[str, Any]:
        """Read frames until one of this type arrives.

        Needed far more here than in the notification tests: one command can
        legitimately produce several frames -- a reply, the thread's own update,
        a read receipt -- and a test that wants one of them should not have to
        know the order the others happen to arrive in.
        """
        while True:
            frame = await self.next_frame(timeout)
            if frame["type"] == wanted:
                return frame

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
