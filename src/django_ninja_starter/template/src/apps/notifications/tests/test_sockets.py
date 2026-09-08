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


async def reply(client: SocketClient, frame: dict[str, Any]) -> dict[str, Any]:
    """Send a command and read past the `state` frame it also broadcasts.

    A change made on this connection is announced to the account's *other*
    connections down the same channel, so this one hears its own announcement
    too -- see `publish_state`. Harmless in a client, noise in a test that wants
    the command's own answer.
    """
    await client.send(frame)
    while True:
        answer = await client.next_frame()
        if answer["type"] != "state":
            return answer


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


def test_the_catch_up_carries_the_broadcasts_it_missed_as_well(alice: Any) -> None:
    """Signing in adds a channel; it does not narrow the connection to that channel.

    Everything unread this account is entitled to see arrives, both audiences in
    one stream and oldest first -- so a client that connected after an
    announcement went out still learns about it, rather than only ever seeing
    the broadcasts published while it happened to be connected.
    """
    token = access_token(alice)
    notify_everyone("Scheduled maintenance")
    notify_user(alice, "Your export is ready")

    async def scenario() -> list[str]:
        client = socket()
        await client.open()
        await client.next_frame()
        await client.command({"command": "authenticate", "token": token})
        subjects = [(await client.next_frame())["notification"]["subject"] for _ in range(2)]
        await client.close()
        return subjects

    assert run(scenario()) == ["Scheduled maintenance", "Your export is ready"]


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


# -- a token on any command -------------------------------------------------


def test_a_token_on_any_command_signs_in_before_running_it(alice: Any) -> None:
    """One frame, not two: the credential travels with the question it is for.

    What comes back is exactly what an `authenticate` followed by the command
    would have sent, in that order, so a client handles one set of frames
    however it chose to present its token.
    """
    notify_user(alice, "Your export is ready")
    token = access_token(alice)

    async def scenario() -> list[dict[str, Any]]:
        client = socket()
        await client.open()
        assert (await client.next_frame())["authenticated"] is False
        await client.send({"command": "unread", "token": token})
        frames = [await client.next_frame() for _ in range(3)]
        await client.close()
        return frames

    signed_in, caught_up, counted = run(scenario())
    assert signed_in["type"] == "authenticated"
    assert signed_in["user"]["username"] == "alice"
    assert caught_up["notification"]["subject"] == "Your export is ready"
    assert counted == {"type": "unread", "count": 1}


def test_a_command_carrying_a_token_for_who_is_already_here_just_runs(alice: Any) -> None:
    """No second welcome. The client did not ask to be told anything twice."""
    notify_user(alice, "Your export is ready")
    token = access_token(alice)
    refreshed = access_token(alice)

    async def scenario() -> dict[str, Any]:
        client = socket(query=f"token={token}")
        await client.open()
        await client.next_frame()
        await client.next_frame()
        counted = await client.command({"command": "unread", "token": refreshed})
        await client.close()
        return counted

    assert run(scenario()) == {"type": "unread", "count": 1}


def test_a_token_that_identifies_nobody_refuses_the_command_with_it(alice: Any) -> None:
    """The refusal comes before the command, so nothing half-happens.

    `read_all` is the one worth proving it on: a token checked afterwards, or
    not at all, would leave the badge cleared by a frame that was answered with
    an error.
    """
    notify_user(alice, "Your export is ready")

    async def scenario() -> dict[str, Any]:
        client = socket()
        await client.open()
        await client.next_frame()
        refusal = await client.command({"command": "read_all", "token": "nope"})
        await client.close()
        return refusal

    refusal = run(scenario())
    assert refusal["type"] == "error"
    assert refusal["title"] == "TOKEN_INVALID"
    assert unread_count(alice) == 1, "the command ran anyway"


def test_a_command_carrying_somebody_elses_token_is_the_same_conflict(alice: Any, bob: Any) -> None:
    """Piggybacking a token is a shorter way to authenticate, not a way around it."""
    notify_user(bob, "For bob only")
    alices = access_token(alice)
    bobs = access_token(bob)

    async def scenario() -> dict[str, Any]:
        client = socket(query=f"token={alices}")
        await client.open()
        await client.next_frame()
        refusal = await client.command({"command": "unread", "token": bobs})
        await client.close()
        return refusal

    assert run(scenario())["title"] == "CONFLICT"


@pytest.mark.parametrize("token", [None, "", "   "])
def test_a_command_with_an_empty_token_is_a_command_with_no_token(
    token: str | None, announcement: Notification
) -> None:
    """Nothing to honour is not the same as a credential that failed.

    A client that sends `token: null` when it has none should meet the ordinary
    refusal for the command it sent, not be told its absent token was invalid.
    """

    async def scenario() -> dict[str, Any]:
        client = socket()
        await client.open()
        await client.next_frame()
        refusal = await client.command(
            {"command": "read", "id": str(announcement.pk), "token": token}
        )
        await client.close()
        return refusal

    assert run(scenario())["title"] == "AUTHENTICATION_REQUIRED"


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
    assert acknowledged == {
        "type": "read",
        "id": str(notification.pk),
        "unread": 1,
        "changed": True,
    }
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


# -- every command, signed in and signed out --------------------------------

OPEN_TO_ANYONE = [
    {"command": "ping"},
    {"command": "whoami"},
    {"command": "deauthenticate"},
]

NEEDS_AN_ACCOUNT = [
    {"command": "list"},
    {"command": "get", "id": "00000000-0000-0000-0000-000000000000"},
    {"command": "count"},
    {"command": "unread"},
    {"command": "read", "id": "00000000-0000-0000-0000-000000000000"},
    {"command": "unread_one", "id": "00000000-0000-0000-0000-000000000000"},
    {"command": "read_all"},
    {"command": "dismiss", "id": "00000000-0000-0000-0000-000000000000"},
    {"command": "restore", "id": "00000000-0000-0000-0000-000000000000"},
    {"command": "dismiss_all"},
]


def _identify(frame: dict[str, Any]) -> str:
    return str(frame["command"])


@pytest.mark.parametrize("frame", OPEN_TO_ANYONE, ids=_identify)
def test_a_command_open_to_anyone_answers_without_a_credential(frame: dict[str, Any]) -> None:
    """These four are the connection's own housekeeping, not somebody's mail."""

    async def scenario() -> dict[str, Any]:
        client = socket()
        await client.open()
        await client.next_frame()
        answer = await client.command(frame)
        await client.close()
        return answer

    assert run(scenario())["type"] != "error"


@pytest.mark.parametrize("frame", NEEDS_AN_ACCOUNT, ids=_identify)
def test_every_other_command_is_refused_without_a_credential(frame: dict[str, Any]) -> None:
    """One refusal, in the same words, for every command that reads somebody's mail."""

    async def scenario() -> dict[str, Any]:
        client = socket()
        await client.open()
        await client.next_frame()
        refusal = await client.command(frame)
        await client.close()
        return refusal

    assert run(scenario())["title"] == "AUTHENTICATION_REQUIRED"


@pytest.mark.parametrize("frame", OPEN_TO_ANYONE + NEEDS_AN_ACCOUNT, ids=_identify)
def test_every_command_answers_once_signed_in(frame: dict[str, Any], alice: Any) -> None:
    """Nothing is refused for want of an account once there is one.

    A missing id is still a `NOT_FOUND`, which is the command running and
    answering -- not the connection turning it away.
    """
    token = access_token(alice)

    async def scenario() -> dict[str, Any]:
        client = socket(query=f"token={token}")
        await client.open()
        await client.next_frame()
        answer = await client.command(frame)
        await client.close()
        return answer

    answer = run(scenario())
    assert answer.get("title") != "AUTHENTICATION_REQUIRED", answer


@pytest.mark.parametrize("frame", NEEDS_AN_ACCOUNT, ids=_identify)
def test_every_command_can_carry_its_own_token(frame: dict[str, Any], alice: Any) -> None:
    """The whole point of a token on any command: one frame, no round trip first."""
    carried = {**frame, "token": access_token(alice)}

    async def scenario() -> list[dict[str, Any]]:
        client = socket()
        await client.open()
        await client.next_frame()
        await client.send(carried)
        signed_in = await client.next_frame()
        answer = await client.next_frame()
        await client.close()
        return [signed_in, answer]

    signed_in, answer = run(scenario())
    assert signed_in["type"] == "authenticated"
    assert answer.get("title") != "AUTHENTICATION_REQUIRED", answer


# -- reading the history over the socket ------------------------------------


def test_the_list_command_returns_the_same_page_the_endpoint_would(alice: Any) -> None:
    notify_user(alice, "Mine")
    notify_everyone("Everybody's")
    token = access_token(alice)

    async def scenario() -> dict[str, Any]:
        client = socket(query=f"token={token}")
        await client.open()
        await client.next_frame()
        for _ in range(2):
            await client.next_frame()
        listed = await client.command({"command": "list"})
        await client.close()
        return listed

    listed = run(scenario())
    assert listed["type"] == "list"
    assert {row["subject"] for row in listed["notifications"]} == {"Mine", "Everybody's"}
    assert listed["total"] == 2


def test_the_list_command_takes_the_endpoints_filters(alice: Any) -> None:
    notify_user(alice, "Mine")
    notify_everyone("Everybody's")
    token = access_token(alice)

    async def scenario() -> dict[str, Any]:
        client = socket(query=f"token={token}")
        await client.open()
        await client.next_frame()
        for _ in range(2):
            await client.next_frame()
        listed = await client.command({"command": "list", "audience": "global"})
        await client.close()
        return listed

    listed = run(scenario())
    assert [row["subject"] for row in listed["notifications"]] == ["Everybody's"]
    assert listed["total"] == 1


def test_the_list_command_pages(alice: Any) -> None:
    for index in range(5):
        notify_user(alice, f"#{index}")
    token = access_token(alice)

    async def scenario() -> dict[str, Any]:
        client = socket(query=f"token={token}")
        await client.open()
        await client.next_frame()
        for _ in range(5):
            await client.next_frame()
        listed = await client.command({"command": "list", "limit": 2, "offset": 1})
        await client.close()
        return listed

    listed = run(scenario())
    assert len(listed["notifications"]) == 2
    assert (listed["limit"], listed["offset"], listed["total"]) == (2, 1, 5)


@pytest.mark.parametrize(
    "frame",
    [
        {"command": "list", "limit": "lots"},
        {"command": "list", "offset": 1.5},
        {"command": "list", "unread": "yes"},
        {"command": "count", "unread": 1},
    ],
    ids=["limit", "offset", "unread flag", "count flag"],
)
def test_an_argument_of_the_wrong_type_is_refused_rather_than_coerced(
    frame: dict[str, Any], alice: Any
) -> None:
    """`{"limit": "all"}` quietly returning fifty rows is worse than an error."""
    token = access_token(alice)

    async def scenario() -> dict[str, Any]:
        client = socket(query=f"token={token}")
        await client.open()
        await client.next_frame()
        refusal = await client.command(frame)
        await client.close()
        return refusal

    assert run(scenario())["title"] == "BAD_REQUEST"


def test_the_get_command_returns_one_notification(alice: Any) -> None:
    notification = notify_user(alice, "Just this one")
    token = access_token(alice)

    async def scenario() -> dict[str, Any]:
        client = socket(query=f"token={token}")
        await client.open()
        await client.next_frame()
        await client.next_frame()
        got = await client.command({"command": "get", "id": str(notification.pk)})
        await client.close()
        return got

    got = run(scenario())
    assert got["type"] == "notification"
    assert got["notification"]["subject"] == "Just this one"


def test_the_get_command_refuses_somebody_elses(alice: Any, bob: Any) -> None:
    theirs = notify_user(bob, "For bob only")
    token = access_token(alice)

    async def scenario() -> dict[str, Any]:
        client = socket(query=f"token={token}")
        await client.open()
        await client.next_frame()
        refusal = await client.command({"command": "get", "id": str(theirs.pk)})
        await client.close()
        return refusal

    assert run(scenario())["title"] == "NOT_FOUND"


def test_the_count_command_answers_a_filtered_total(alice: Any) -> None:
    notify_user(alice, "Fine", level="info")
    notify_user(alice, "Broken", level="error")
    token = access_token(alice)

    async def scenario() -> dict[str, Any]:
        client = socket(query=f"token={token}")
        await client.open()
        await client.next_frame()
        for _ in range(2):
            await client.next_frame()
        counted = await client.command({"command": "count", "level": "error"})
        await client.close()
        return counted

    assert run(scenario()) == {"type": "count", "count": 1}


# -- changing state over the socket -----------------------------------------


def test_unreading_over_the_socket_puts_it_back_in_the_badge(alice: Any) -> None:
    notification = notify_user(alice, "Your export is ready")
    token = access_token(alice)

    async def scenario() -> dict[str, Any]:
        client = socket(query=f"token={token}")
        await client.open()
        await client.next_frame()
        await client.next_frame()
        await reply(client, {"command": "read", "id": str(notification.pk)})
        undone = await reply(client, {"command": "unread_one", "id": str(notification.pk)})
        await client.close()
        return undone

    undone = run(scenario())
    assert undone["type"] == "unread_one"
    assert undone["unread"] == 1
    assert unread_count(alice) == 1


def test_dismissing_over_the_socket_clears_it_from_the_tray(alice: Any) -> None:
    notification = notify_user(alice, "Your export is ready")
    token = access_token(alice)

    async def scenario() -> list[dict[str, Any]]:
        client = socket(query=f"token={token}")
        await client.open()
        await client.next_frame()
        await client.next_frame()
        dismissed = await reply(client, {"command": "dismiss", "id": str(notification.pk)})
        listed = await reply(client, {"command": "list"})
        await client.close()
        return [dismissed, listed]

    dismissed, listed = run(scenario())
    assert dismissed["type"] == "dismiss"
    assert dismissed["unread"] == 0
    assert listed["notifications"] == []


def test_restoring_over_the_socket_puts_it_back(alice: Any) -> None:
    notification = notify_user(alice, "Your export is ready")
    token = access_token(alice)

    async def scenario() -> list[dict[str, Any]]:
        client = socket(query=f"token={token}")
        await client.open()
        await client.next_frame()
        await client.next_frame()
        await reply(client, {"command": "dismiss", "id": str(notification.pk)})
        restored = await reply(client, {"command": "restore", "id": str(notification.pk)})
        listed = await reply(client, {"command": "list"})
        await client.close()
        return [restored, listed]

    restored, listed = run(scenario())
    assert restored["type"] == "restore"
    assert len(listed["notifications"]) == 1


def test_dismissing_everything_over_the_socket_empties_the_tray(alice: Any) -> None:
    notify_user(alice, "One")
    notify_everyone("Two")
    token = access_token(alice)

    async def scenario() -> dict[str, Any]:
        client = socket(query=f"token={token}")
        await client.open()
        await client.next_frame()
        for _ in range(2):
            await client.next_frame()
        cleared = await client.command({"command": "dismiss_all"})
        await client.close()
        return cleared

    assert run(scenario()) == {"type": "dismiss_all", "count": 2, "unread": 0}


def test_a_repeated_change_is_answered_with_changed_false(alice: Any) -> None:
    """Success either way: a client that fired twice should not have to care."""
    notification = notify_user(alice, "Your export is ready")
    token = access_token(alice)

    async def scenario() -> dict[str, Any]:
        client = socket(query=f"token={token}")
        await client.open()
        await client.next_frame()
        await client.next_frame()
        await reply(client, {"command": "read", "id": str(notification.pk)})
        again = await reply(client, {"command": "read", "id": str(notification.pk)})
        await client.close()
        return again

    assert run(scenario())["changed"] is False


@pytest.mark.parametrize(
    "command", ["read", "unread_one", "dismiss", "restore", "get"], ids=lambda name: str(name)
)
def test_every_per_notification_command_refuses_an_id_that_is_not_one(
    command: str, alice: Any
) -> None:
    token = access_token(alice)

    async def scenario() -> dict[str, Any]:
        client = socket(query=f"token={token}")
        await client.open()
        await client.next_frame()
        refusal = await client.command({"command": command, "id": "banana"})
        await client.close()
        return refusal

    assert run(scenario())["title"] == "BAD_REQUEST"


# -- keeping a second device in step ----------------------------------------


def test_reading_on_one_connection_tells_this_accounts_others(alice: Any) -> None:
    """The reason `state` is a frame type: a badge cleared here clears there."""
    notification = notify_user(alice, "Your export is ready")
    token = access_token(alice)

    async def scenario() -> dict[str, Any]:
        phone = socket(query=f"token={token}")
        laptop = socket(query=f"token={token}")
        for client in (phone, laptop):
            await client.open()
            await client.next_frame()
            await client.next_frame()
        await phone.command({"command": "read", "id": str(notification.pk)})
        told = await laptop.next_frame()
        await phone.close()
        await laptop.close()
        return told

    told = run(scenario())
    assert told["type"] == "state"
    assert told["action"] == "read"
    assert told["unread"] == 0
    assert told["ids"] == [str(notification.pk)]


def test_reading_everything_tells_this_accounts_others_too(alice: Any) -> None:
    notify_user(alice, "One")
    token = access_token(alice)

    async def scenario() -> dict[str, Any]:
        phone = socket(query=f"token={token}")
        laptop = socket(query=f"token={token}")
        for client in (phone, laptop):
            await client.open()
            await client.next_frame()
            await client.next_frame()
        await phone.command({"command": "read_all"})
        told = await laptop.next_frame()
        await phone.close()
        await laptop.close()
        return told

    told = run(scenario())
    assert (told["type"], told["action"], told["unread"]) == ("state", "read_all", 0)


def test_a_change_that_changed_nothing_wakes_nobody(alice: Any) -> None:
    """Otherwise a client polling `read` would flood every other device."""
    notification = notify_user(alice, "Your export is ready")
    token = access_token(alice)

    async def scenario() -> dict[str, Any]:
        phone = socket(query=f"token={token}")
        laptop = socket(query=f"token={token}")
        for client in (phone, laptop):
            await client.open()
            await client.next_frame()
            await client.next_frame()
        await phone.command({"command": "read", "id": str(notification.pk)})
        await laptop.next_frame()
        await phone.command({"command": "read", "id": str(notification.pk)})
        await phone.command({"command": "ping"})
        # The pong proves the second read produced no state frame: had it done,
        # it would be sitting in front of the pong on the laptop's queue.
        await laptop.send({"command": "ping"})
        next_up = await laptop.next_frame()
        await phone.close()
        await laptop.close()
        return next_up

    assert run(scenario())["type"] == "pong"


def test_another_accounts_state_never_arrives(alice: Any, bob: Any) -> None:
    theirs = notify_user(bob, "For bob only")
    mine, hers = access_token(alice), access_token(bob)

    async def scenario() -> dict[str, Any]:
        watcher = socket(query=f"token={mine}")
        other = socket(query=f"token={hers}")
        for client, count in ((watcher, 0), (other, 1)):
            await client.open()
            await client.next_frame()
            for _ in range(count):
                await client.next_frame()
        await other.command({"command": "read", "id": str(theirs.pk)})
        await watcher.send({"command": "ping"})
        next_up = await watcher.next_frame()
        await watcher.close()
        await other.close()
        return next_up

    assert run(scenario())["type"] == "pong"


# -- whoami and signing out -------------------------------------------------


def test_whoami_names_the_account_when_there_is_one(alice: Any) -> None:
    notify_user(alice, "One")
    token = access_token(alice)

    async def scenario() -> dict[str, Any]:
        client = socket(query=f"token={token}")
        await client.open()
        await client.next_frame()
        await client.next_frame()
        who = await client.command({"command": "whoami"})
        await client.close()
        return who

    who = run(scenario())
    assert who["authenticated"] is True
    assert who["user"]["username"] == "alice"
    assert who["unread"] == 1


def test_whoami_answers_rather_than_refuses_when_there_is_nobody() -> None:
    """ "You are nobody" is the useful reply -- a reconnecting client asks because
    it does not know."""

    async def scenario() -> dict[str, Any]:
        client = socket()
        await client.open()
        await client.next_frame()
        who = await client.command({"command": "whoami"})
        await client.close()
        return who

    assert run(scenario()) == {
        "type": "whoami",
        "authenticated": False,
        "user": None,
        "unread": 0,
    }


def test_signing_out_keeps_the_connection_and_the_public_feed(alice: Any) -> None:
    """A shared browser signing out should still see the announcements."""
    token = access_token(alice)

    async def scenario() -> list[dict[str, Any]]:
        client = socket(query=f"token={token}")
        await client.open()
        await client.next_frame()
        gone = await client.command({"command": "deauthenticate"})
        await sync_to_async(notify_everyone)("Still for everybody")
        public = await client.next_frame()
        await client.close()
        return [gone, public]

    gone, public = run(scenario())
    assert gone["type"] == "deauthenticated"
    assert gone["user"]["username"] == "alice"
    assert public["notification"]["subject"] == "Still for everybody"


def test_signing_out_stops_this_accounts_private_traffic(alice: Any) -> None:
    """The subscription cannot leave a channel, so the frames are filtered instead."""
    token = access_token(alice)

    async def scenario() -> dict[str, Any]:
        client = socket(query=f"token={token}")
        await client.open()
        await client.next_frame()
        await client.command({"command": "deauthenticate"})
        await sync_to_async(notify_user)(alice, "Private again")
        await sync_to_async(notify_everyone)("Public")
        frame = await client.next_frame()
        await client.close()
        return frame

    assert run(scenario())["notification"]["subject"] == "Public"


def test_signing_out_puts_the_private_commands_back_behind_the_credential(alice: Any) -> None:
    token = access_token(alice)

    async def scenario() -> dict[str, Any]:
        client = socket(query=f"token={token}")
        await client.open()
        await client.next_frame()
        await client.command({"command": "deauthenticate"})
        refusal = await client.command({"command": "unread"})
        await client.close()
        return refusal

    assert run(scenario())["title"] == "AUTHENTICATION_REQUIRED"


def test_signing_back_in_after_signing_out_works(alice: Any) -> None:
    """Not a conflict: the connection stopped claiming that account when it left."""
    token = access_token(alice)

    async def scenario() -> dict[str, Any]:
        client = socket(query=f"token={token}")
        await client.open()
        await client.next_frame()
        await client.command({"command": "deauthenticate"})
        back = await client.command({"command": "authenticate", "token": token})
        await client.close()
        return back

    back = run(scenario())
    assert back["type"] == "authenticated"
    assert back["user"]["username"] == "alice"


def test_signing_out_when_nobody_was_signed_in_is_answered_not_refused() -> None:
    async def scenario() -> dict[str, Any]:
        client = socket()
        await client.open()
        await client.next_frame()
        gone = await client.command({"command": "deauthenticate"})
        await client.close()
        return gone

    assert run(scenario()) == {"type": "deauthenticated", "user": None}


def test_the_list_command_honours_the_unread_flag(alice: Any) -> None:
    """The flag is three-valued, so `true` has to be as real a case as absent."""
    read_already = notify_user(alice, "Old news")
    notify_user(alice, "Still outstanding")
    token = access_token(alice)

    async def scenario() -> list[dict[str, Any]]:
        client = socket(query=f"token={token}")
        await client.open()
        await client.next_frame()
        for _ in range(2):
            await client.next_frame()
        await reply(client, {"command": "read", "id": str(read_already.pk)})
        outstanding = await reply(client, {"command": "list", "unread": True})
        done = await reply(client, {"command": "list", "unread": False})
        await client.close()
        return [outstanding, done]

    outstanding, done = run(scenario())
    assert [row["subject"] for row in outstanding["notifications"]] == ["Still outstanding"]
    assert [row["subject"] for row in done["notifications"]] == ["Old news"]
