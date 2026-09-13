"""Where a browser is allowed to open one of this project's sockets from.

Browsers do not apply the same-origin policy to WebSockets: any page may ask for
a socket to any host, and the browser sends the handshake with cookies attached.
These sockets accept a session cookie as identity, so the handshake is the only
place a page that is not ours can be turned away.
"""

import asyncio
from typing import Any

from config.sockets import BAD_ORIGIN, allowed_origins, origin_allowed, websocket_application


def _scope(origin: str | None = None, path: str = "/ws/notifications") -> dict[str, Any]:
    headers = [(b"origin", origin.encode())] if origin is not None else []
    return {"type": "websocket", "path": path, "headers": headers, "query_string": b""}


def test_a_page_this_deployment_serves_is_allowed(settings: Any) -> None:
    settings.ALLOWED_HOSTS = ["example.test"]

    assert origin_allowed(_scope("https://example.test"))


def test_somebody_elses_page_is_not(settings: Any) -> None:
    """The whole point: a signed-in person visiting evil.test must not have
    their notification feed opened and read by it."""
    settings.ALLOWED_HOSTS = ["example.test"]

    assert not origin_allowed(_scope("https://evil.test"))


def test_a_lookalike_host_is_not_allowed_either(settings: Any) -> None:
    """Compared as a host, not as a prefix -- `example.test.evil.test` is not us."""
    settings.ALLOWED_HOSTS = ["example.test"]

    assert not origin_allowed(_scope("https://example.test.evil.test"))


def test_the_port_a_page_is_served_on_does_not_change_who_it_is(settings: Any) -> None:
    settings.ALLOWED_HOSTS = ["localhost"]

    assert origin_allowed(_scope("http://localhost:5173"))


def test_a_handshake_with_no_origin_is_allowed(settings: Any) -> None:
    """A native client, a server-to-server consumer or a test.

    None of them is a browser, so none of them carries ambient credentials
    somebody else's page can borrow -- and every one of them would otherwise be
    broken by this check.
    """
    settings.ALLOWED_HOSTS = ["example.test"]

    assert origin_allowed(_scope(None))


def test_a_frontend_on_another_domain_can_be_named(settings: Any) -> None:
    settings.ALLOWED_HOSTS = ["api.example.test"]
    settings.WEBSOCKET_ALLOWED_ORIGINS = ["app.example.test"]

    assert origin_allowed(_scope("https://app.example.test"))
    assert not origin_allowed(_scope("https://api.example.test"))


def test_naming_a_star_turns_the_check_off(settings: Any) -> None:
    settings.WEBSOCKET_ALLOWED_ORIGINS = ["*"]

    assert origin_allowed(_scope("https://anywhere.test"))


def test_it_follows_allowed_hosts_when_nothing_is_named(settings: Any) -> None:
    settings.ALLOWED_HOSTS = ["example.test"]
    settings.WEBSOCKET_ALLOWED_ORIGINS = []

    assert allowed_origins() == ["example.test"]


def test_a_refused_origin_closes_the_handshake(settings: Any) -> None:
    """Closed with its own code, so a client can tell this from a wrong path."""
    settings.ALLOWED_HOSTS = ["example.test"]
    sent: list[dict[str, Any]] = []

    async def receive() -> dict[str, str]:
        return {"type": "websocket.connect"}

    async def send(message: dict[str, Any]) -> None:
        sent.append(message)

    asyncio.run(websocket_application(_scope("https://evil.test"), receive, send))

    assert sent == [{"type": "websocket.close", "code": BAD_ORIGIN}]
