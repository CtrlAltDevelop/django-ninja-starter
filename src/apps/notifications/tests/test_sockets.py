"""The socket, driven frame by frame.

These run against a real database rather than a wrapped transaction, because the
consumer reaches the ORM through ``sync_to_async`` -- which means a different
thread, which means a different connection, which would not see rows that were
never committed. It is also what makes ``transaction.on_commit`` fire, and
``on_commit`` is what publishes; a suite that skipped it would be testing a
delivery path that does not exist in production.
"""

import asyncio
from typing import Any

import pytest
from asgiref.sync import sync_to_async
from django.test import Client, override_settings

from apps.notifications.events import notify_everyone, notify_user
from apps.notifications.models import Notification, unread_count
from apps.notifications.sockets import notifications_socket
from apps.notifications.tests.conftest import SocketClient, access_token

pytestmark = pytest.mark.django_db(transaction=True)


def run(scenario: Any) -> Any:
    """Run one socket conversation to completion."""
    return asyncio.run(asyncio.wait_for(scenario, timeout=30))


def socket(**kwargs: Any) -> SocketClient:
    return SocketClient(notifications_socket, **kwargs)


# -- connecting -------------------------------------------------------------


def test_a_connection_with_no_credential_is_accepted_and_told_so() -> None:
    """The point of the design: the public feed needs no account."""

    async def scenario() -> dict[str, Any]:
        client = socket()
        assert (await client.open())["type"] == "websocket.accept"
        ready = await client.next_frame()
        await client.close()
        return ready

    ready = run(scenario())
    assert ready == {"type": "ready", "authenticated": False, "user": None, "unread": 0}


def test_a_broadcast_reaches_a_connection_that_never_authenticated() -> None:
    async def scenario() -> dict[str, Any]:
        client = socket()
        await client.open()
        await client.next_frame()
        await sync_to_async(notify_everyone)("Scheduled maintenance")
        frame = await client.next_frame()
        await client.close()
        return frame

    frame = run(scenario())
    assert frame["type"] == "notification"
    assert frame["notification"]["subject"] == "Scheduled maintenance"
    assert frame["notification"]["audience"] == "global"
    assert frame["notification"]["read"] is False


def test_a_token_in_the_query_string_authenticates_at_connect(alice: Any) -> None:
    """A client that already knows who it is should not need the extra round trip."""
    token = access_token(alice)

    async def scenario() -> dict[str, Any]:
        client = socket(query=f"token={token}")
        await client.open()
        ready = await client.next_frame()
        await client.close()
        return ready

    ready = run(scenario())
    assert ready["authenticated"] is True
    assert ready["user"]["username"] == "alice"


def test_a_bearer_subprotocol_authenticates_and_is_echoed_back(alice: Any) -> None:
    """A browser cannot set headers on a WebSocket; this is the one it can set.

    Echoing it is not decoration -- a browser that offered a subprotocol and got
    none back closes the connection itself.
    """
    token = access_token(alice)

    async def scenario() -> tuple[dict[str, Any], dict[str, Any]]:
        client = socket(headers=[(b"sec-websocket-protocol", f"bearer, {token}".encode())])
        accept = await client.open()
        ready = await client.next_frame()
        await client.close()
        return accept, ready

    accept, ready = run(scenario())
    assert accept["subprotocol"] == "bearer"
    assert ready["authenticated"] is True


def test_an_authorization_header_authenticates_at_connect(alice: Any) -> None:
    token = access_token(alice)

    async def scenario() -> dict[str, Any]:
        client = socket(headers=[(b"authorization", f"Bearer {token}".encode())])
        await client.open()
        ready = await client.next_frame()
        await client.close()
        return ready

    assert run(scenario())["authenticated"] is True


def test_a_session_cookie_authenticates_a_browser_that_has_no_token(alice: Any) -> None:
    """Django's own login is an identity too, and a browser sends it unprompted."""
    browser = Client()
    browser.force_login(alice)
    cookie = f"sessionid={browser.cookies['sessionid'].value}".encode()

    async def scenario() -> dict[str, Any]:
        client = socket(headers=[(b"cookie", cookie)])
        await client.open()
        ready = await client.next_frame()
        await client.close()
        return ready

    assert run(scenario())["user"]["username"] == "alice"


def test_a_junk_token_in_the_handshake_connects_as_nobody(alice: Any) -> None:
    """Refusing the handshake would deny the public feed over a private credential."""

    async def scenario() -> dict[str, Any]:
        client = socket(query="token=not-a-real-token")
        await client.open()
        ready = await client.next_frame()
        await client.close()
        return ready

    assert run(scenario())["authenticated"] is False


# -- authenticating over the connection -------------------------------------


def test_authenticating_unlocks_this_accounts_own_notifications(alice: Any) -> None:
    """Connect, hear the public traffic, say who you are, hear your own."""
    token = access_token(alice)

    async def scenario() -> tuple[dict[str, Any], dict[str, Any]]:
        client = socket()
        await client.open()
        await client.next_frame()
        authenticated = await client.command({"command": "authenticate", "token": token})
        await sync_to_async(notify_user)(alice, "Your export is ready")
        delivered = await client.next_frame()
        await client.close()
        return authenticated, delivered

    authenticated, delivered = run(scenario())
    assert authenticated["type"] == "authenticated"
    assert authenticated["user"]["username"] == "alice"
    assert delivered["notification"]["subject"] == "Your export is ready"


def test_an_authenticated_connection_still_receives_the_public_feed(alice: Any) -> None:
    """One socket carries both audiences; a client should not need two."""
    token = access_token(alice)

    async def scenario() -> dict[str, Any]:
        client = socket(query=f"token={token}")
        await client.open()
        await client.next_frame()
        await sync_to_async(notify_everyone)("Everybody")
        frame = await client.next_frame()
        await client.close()
        return frame

    assert run(scenario())["notification"]["subject"] == "Everybody"


def test_another_accounts_notification_never_arrives(alice: Any, bob: Any) -> None:
    """Asserted by ordering: bob's is created first, and alice's is what turns up."""
    token = access_token(alice)

    async def scenario() -> dict[str, Any]:
        client = socket(query=f"token={token}")
        await client.open()
        await client.next_frame()
        await sync_to_async(notify_user)(bob, "For bob only")
        await sync_to_async(notify_user)(alice, "For alice")
        frame = await client.next_frame()
        await client.close()
        return frame

    assert run(scenario())["notification"]["subject"] == "For alice"


def test_authenticating_catches_a_client_up_on_what_it_missed(alice: Any) -> None:
    token = access_token(alice)
    notify_user(alice, "Older")
    notify_user(alice, "Newer")

    async def scenario() -> list[str]:
        client = socket()
        await client.open()
        await client.next_frame()
        await client.command({"command": "authenticate", "token": token})
        subjects = [(await client.next_frame())["notification"]["subject"] for _ in range(2)]
        await client.close()
        return subjects

    assert run(scenario()) == ["Older", "Newer"]


def test_the_catch_up_can_be_turned_off(alice: Any) -> None:
    notify_user(alice, "Older")
    token = access_token(alice)

    async def scenario() -> dict[str, Any]:
        client = socket(query=f"token={token}")
        await client.open()
        await client.next_frame()
        await client.send({"command": "ping"})
        following = await client.next_frame()
        await client.close()
        return following

    with override_settings(NOTIFICATIONS_SOCKET_BACKLOG=0):
        assert run(scenario()) == {"type": "pong"}


def test_a_token_that_identifies_nobody_is_an_error_frame_not_a_close() -> None:
    """One mistyped credential should not cost the public feed as well."""

    async def scenario() -> tuple[dict[str, Any], dict[str, Any]]:
        client = socket()
        await client.open()
        await client.next_frame()
        refusal = await client.command({"command": "authenticate", "token": "nope"})
        still_alive = await client.command({"command": "ping"})
        await client.close()
        return refusal, still_alive

    refusal, still_alive = run(scenario())
    assert refusal["type"] == "error"
    assert refusal["title"] == "TOKEN_INVALID"
    assert still_alive == {"type": "pong"}


def test_authenticating_twice_as_the_same_account_is_allowed(alice: Any) -> None:
    """A client that refreshed its token should not have to reconnect."""
    token = access_token(alice)
    refreshed = access_token(alice)

    async def scenario() -> dict[str, Any]:
        client = socket(query=f"token={token}")
        await client.open()
        await client.next_frame()
        again = await client.command({"command": "authenticate", "token": refreshed})
        await client.close()
        return again

    assert run(scenario())["type"] == "authenticated"


def test_authenticating_as_somebody_else_is_refused(alice: Any, bob: Any) -> None:
    """This connection is already joined to alice's channel; there is no honest
    way to serve two people down one socket."""
    alices = access_token(alice)
    bobs = access_token(bob)

    async def scenario() -> dict[str, Any]:
        client = socket(query=f"token={alices}")
        await client.open()
        await client.next_frame()
        refusal = await client.command({"command": "authenticate", "token": bobs})
        await client.close()
        return refusal

    refusal = run(scenario())
    assert refusal["title"] == "CONFLICT"


# -- reading over the socket ------------------------------------------------


def test_reading_a_notification_over_the_socket_marks_it_read(alice: Any) -> None:
    notification = notify_user(alice, "Your export is ready")
    notify_everyone("And an announcement")
    token = access_token(alice)

    async def scenario() -> dict[str, Any]:
        client = socket(query=f"token={token}")
        await client.open()
        await client.next_frame()
        for _ in range(2):
            await client.next_frame()
        acknowledged = await client.command({"command": "read", "id": str(notification.pk)})
        await client.close()
        return acknowledged

    acknowledged = run(scenario())
    assert acknowledged == {"type": "read", "id": str(notification.pk), "unread": 1}
    assert unread_count(alice) == 1


def test_reading_everything_over_the_socket_clears_the_badge(alice: Any) -> None:
    notify_user(alice, "One")
    notify_everyone("Two")
    token = access_token(alice)

    async def scenario() -> dict[str, Any]:
        client = socket(query=f"token={token}")
        await client.open()
        await client.next_frame()
        for _ in range(2):
            await client.next_frame()
        cleared = await client.command({"command": "read_all"})
        await client.close()
        return cleared

    assert run(scenario()) == {"type": "read_all", "count": 2, "unread": 0}
    assert unread_count(alice) == 0


def test_the_unread_command_answers_with_the_badge_number(alice: Any) -> None:
    notify_user(alice, "One")
    token = access_token(alice)

    async def scenario() -> dict[str, Any]:
        client = socket(query=f"token={token}")
        await client.open()
        await client.next_frame()
        await client.next_frame()
        counted = await client.command({"command": "unread"})
        await client.close()
        return counted

    assert run(scenario()) == {"type": "unread", "count": 1}


def test_reading_before_authenticating_is_refused(announcement: Notification) -> None:
    async def scenario() -> dict[str, Any]:
        client = socket()
        await client.open()
        await client.next_frame()
        refusal = await client.command({"command": "read", "id": str(announcement.pk)})
        await client.close()
        return refusal

    assert run(scenario())["title"] == "AUTHENTICATION_REQUIRED"


def test_reading_somebody_elses_notification_is_a_not_found(alice: Any, bob: Any) -> None:
    theirs = notify_user(bob, "For bob only")
    token = access_token(alice)

    async def scenario() -> dict[str, Any]:
        client = socket(query=f"token={token}")
        await client.open()
        await client.next_frame()
        refusal = await client.command({"command": "read", "id": str(theirs.pk)})
        await client.close()
        return refusal

    assert run(scenario())["title"] == "NOT_FOUND"


def test_an_id_that_is_not_an_id_is_a_bad_request(alice: Any) -> None:
    token = access_token(alice)

    async def scenario() -> dict[str, Any]:
        client = socket(query=f"token={token}")
        await client.open()
        await client.next_frame()
        refusal = await client.command({"command": "read", "id": "banana"})
        await client.close()
        return refusal

    assert run(scenario())["title"] == "BAD_REQUEST"


# -- malformed traffic ------------------------------------------------------


@pytest.mark.parametrize(
    "frame",
    [{"command": "teleport"}, {"nothing": "useful"}],
    ids=["unknown command", "no command"],
)
def test_a_command_the_socket_does_not_know_is_an_error_frame(frame: dict[str, Any]) -> None:
    async def scenario() -> dict[str, Any]:
        client = socket()
        await client.open()
        await client.next_frame()
        refusal = await client.command(frame)
        await client.close()
        return refusal

    assert run(scenario())["title"] == "BAD_REQUEST"


@pytest.mark.parametrize("text", ["{not json", '"a string"'], ids=["not json", "not an object"])
def test_a_frame_that_is_not_a_json_object_is_an_error_frame(text: str) -> None:
    async def scenario() -> dict[str, Any]:
        client = socket()
        await client.open()
        await client.next_frame()
        await client.send_text(text)
        refusal = await client.next_frame()
        await client.close()
        return refusal

    assert run(scenario())["title"] == "BAD_REQUEST"


def test_a_binary_frame_is_refused_without_closing_the_connection() -> None:
    async def scenario() -> dict[str, Any]:
        client = socket()
        await client.open()
        await client.next_frame()
        await client._to_server.put({"type": "websocket.receive", "bytes": b"\x00\x01"})
        refusal = await client.next_frame()
        await client.close()
        return refusal

    assert run(scenario())["title"] == "BAD_REQUEST"
