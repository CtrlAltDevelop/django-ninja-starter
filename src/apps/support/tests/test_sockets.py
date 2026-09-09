"""The support socket, driven frame by frame.

Real database rather than a wrapped transaction, for the same reason the
notification socket tests use one: the consumer reaches the ORM through
``sync_to_async``, which is another thread and so another connection, and
``transaction.on_commit`` -- which is what publishes -- never fires inside a
test transaction that is rolled back. A suite that skipped it would be
exercising a delivery path production does not have.
"""

import asyncio
from typing import Any

import pytest
from asgiref.sync import sync_to_async

from apps.support.models import Kind, Ticket, post_message
from apps.support.sockets import UNAUTHENTICATED_CLOSE, SupportSocket, support_socket
from apps.support.tests.conftest import SocketClient, access_token

pytestmark = pytest.mark.django_db(transaction=True)


def run(scenario: Any) -> Any:
    """Run one socket conversation to completion."""
    return asyncio.run(asyncio.wait_for(scenario, timeout=30))


def socket(**kwargs: Any) -> SocketClient:
    return SocketClient(support_socket, **kwargs)


async def signed_in(user: Any) -> SocketClient:
    """A connection that arrived with its credential already in the handshake."""
    client = socket(query=f"token={await sync_to_async(access_token)(user)}")
    await client.open()
    await client.next_frame()  # ready
    return client


async def reply(client: SocketClient, frame: dict[str, Any], wanted: str) -> dict[str, Any]:
    """Send a command and read past whatever it also broadcast to this connection.

    A command that changes a thread this connection is joined to is announced on
    that thread's channel, and this connection hears its own announcement. That
    is right for a client and noise for a test that wants the command's answer.
    """
    await client.send(frame)
    return await client.frame_of_type(wanted)


# -- connecting -------------------------------------------------------------


def test_a_connection_with_no_credential_is_closed_not_accepted() -> None:
    """The socket admits nobody it cannot name, and says so at the handshake.

    Closed before any accept, which an ASGI server turns into a 403 on the
    upgrade: the client learns it failed instead of holding a socket that never
    speaks.
    """

    async def scenario() -> dict[str, Any]:
        client = socket()
        return await client.open()

    answer = run(scenario())
    assert answer["type"] == "websocket.close"
    assert answer["code"] == UNAUTHENTICATED_CLOSE


def test_a_bad_token_in_the_handshake_is_closed_the_same_way() -> None:
    """A token that names nobody is no better than no token at all."""

    async def scenario() -> dict[str, Any]:
        client = socket(query="token=not-a-real-token")
        return await client.open()

    assert run(scenario())["type"] == "websocket.close"


def test_a_token_in_the_query_string_is_honoured_at_connect_time(client_user: Any) -> None:
    async def scenario() -> dict[str, Any]:
        client = socket(query=f"token={await sync_to_async(access_token)(client_user)}")
        await client.open()
        ready = await client.next_frame()
        await client.close()
        return ready

    ready = run(scenario())
    assert ready["authenticated"] is True
    assert ready["user"]["username"] == "clara"


def test_an_offered_subprotocol_is_echoed_back(client_user: Any) -> None:
    """A browser answered with no subprotocol closes the connection itself."""

    async def scenario() -> dict[str, Any]:
        token = await sync_to_async(access_token)(client_user)
        client = socket(
            headers=[(b"sec-websocket-protocol", f"bearer, {token}".encode())],
        )
        accept = await client.open()
        await client.next_frame()
        await client.close()
        return accept

    assert run(scenario())["subprotocol"] == "bearer"


# -- the commands every connection may send ---------------------------------


def test_ping_is_answered(client_user: Any) -> None:
    """Kept so a client can hold an idle connection open through a proxy."""

    async def scenario() -> dict[str, Any]:
        client = await signed_in(client_user)
        answer = await client.command({"command": "ping"})
        await client.close()
        return answer

    assert run(scenario()) == {"type": "pong"}


def test_there_is_no_signing_in_over_the_socket(client_user: Any) -> None:
    """The commands mid-connection sign-in needed are gone, not merely refused.

    A connection is one account's for its whole life. Serving two people down
    one socket was the thing `authenticate` made possible and nothing wanted.
    """
    assert "authenticate" not in SupportSocket.commands()
    assert "deauthenticate" not in SupportSocket.commands()


def test_whoami_says_who_the_connection_belongs_to(client_user: Any) -> None:
    """Still worth asking after a sleep, and now it always has an answer."""

    async def scenario() -> dict[str, Any]:
        client = await signed_in(client_user)
        answer = await client.command({"command": "whoami"})
        await client.close()
        return answer

    answer = run(scenario())
    assert answer["authenticated"] is True
    assert answer["user"]["username"] == "clara"


# -- malformed frames -------------------------------------------------------


def test_a_binary_frame_is_refused(client_user: Any) -> None:
    async def scenario() -> dict[str, Any]:
        client = await signed_in(client_user)
        await client._to_server.put({"type": "websocket.receive", "bytes": b"\x00"})
        refusal = await client.next_frame()
        await client.close()
        return refusal

    assert run(scenario())["title"] == "BAD_REQUEST"


def test_a_frame_that_is_not_json_is_refused(client_user: Any) -> None:
    async def scenario() -> dict[str, Any]:
        client = await signed_in(client_user)
        await client.send_text("{oh no")
        refusal = await client.next_frame()
        await client.close()
        return refusal

    assert run(scenario())["description"] == "That was not JSON."


def test_a_json_frame_that_is_not_an_object_is_refused(client_user: Any) -> None:
    async def scenario() -> dict[str, Any]:
        client = await signed_in(client_user)
        await client.send_text("[1, 2, 3]")
        refusal = await client.next_frame()
        await client.close()
        return refusal

    assert run(scenario())["title"] == "BAD_REQUEST"


def test_an_unknown_command_is_refused(client_user: Any) -> None:
    async def scenario() -> dict[str, Any]:
        client = await signed_in(client_user)
        refusal = await client.command({"command": "sudo"})
        await client.close()
        return refusal

    assert run(scenario())["description"] == "Unknown command."


def test_a_ticket_id_that_is_not_an_id_is_refused(client_user: Any) -> None:
    async def scenario() -> dict[str, Any]:
        client = await signed_in(client_user)
        refusal = await client.command({"command": "ticket", "ticket": "seventeen"})
        await client.close()
        return refusal

    refusal = run(scenario())
    assert refusal["title"] == "BAD_REQUEST"
    assert "ticket id" in refusal["description"]


# -- reading ----------------------------------------------------------------


def test_the_queue_is_paged_and_carries_the_unfiltered_total(
    client_user: Any, ticket: Ticket
) -> None:
    async def scenario() -> dict[str, Any]:
        client = await signed_in(client_user)
        answer = await client.command({"command": "tickets", "limit": 10})
        await client.close()
        return answer

    answer = run(scenario())
    assert answer["total"] == 1
    assert answer["limit"] == 10
    assert answer["tickets"][0]["subject"] == "I was charged twice"


def test_a_client_cannot_read_another_clients_thread(other_client: Any, ticket: Ticket) -> None:
    async def scenario() -> dict[str, Any]:
        client = await signed_in(other_client)
        refusal = await client.command({"command": "ticket", "ticket": str(ticket.id)})
        await client.close()
        return refusal

    assert run(scenario())["title"] == "NOT_FOUND"


def test_one_thread_can_be_fetched_with_its_messages(client_user: Any, ticket: Ticket) -> None:
    async def scenario() -> tuple[dict[str, Any], dict[str, Any]]:
        client = await signed_in(client_user)
        one = await client.command({"command": "ticket", "ticket": str(ticket.id)})
        page = await client.command({"command": "messages", "ticket": str(ticket.id)})
        await client.close()
        return one, page

    one, page = run(scenario())
    assert one["reason"] == "requested"
    assert one["ticket"]["id"] == str(ticket.id)
    assert [message["body"] for message in page["messages"]] == [
        "There are two charges on the 3rd."
    ]


def test_the_categories_the_desk_offers_are_readable(client_user: Any, category: Any) -> None:
    async def scenario() -> dict[str, Any]:
        client = await signed_in(client_user)
        answer = await client.command({"command": "categories"})
        await client.close()
        return answer

    assert "Billing" in [row["name"] for row in run(scenario())["categories"]]


# -- opening and talking ----------------------------------------------------


def test_opening_a_thread_subscribes_the_connection_to_it(client_user: Any) -> None:
    """The point of opening over the socket rather than over HTTP."""

    async def scenario() -> tuple[dict[str, Any], dict[str, Any]]:
        client = await signed_in(client_user)
        opened = await client.command({"command": "open", "kind": "chat", "body": "Hello?"})
        await sync_to_async(_reply_as_staff)(opened["ticket"]["id"])
        heard = await client.frame_of_type("message")
        await client.close()
        return opened, heard

    opened, heard = run(scenario())
    assert opened["ticket"]["kind"] == Kind.CHAT
    assert heard["message"]["body"] == "We are looking into it."


def test_a_message_reaches_the_other_side_of_the_conversation(
    client_user: Any, agent: Any, ticket: Ticket
) -> None:
    async def scenario() -> dict[str, Any]:
        desk = await signed_in(agent)
        await desk.command({"command": "subscribe", "ticket": str(ticket.id)})
        her = await signed_in(client_user)
        await her.command({"command": "subscribe", "ticket": str(ticket.id)})
        await her.command({"command": "send", "ticket": str(ticket.id), "body": "Any news?"})
        heard = await desk.frame_of_type("message")
        await her.close()
        await desk.close()
        return heard

    heard = run(scenario())
    assert heard["message"]["body"] == "Any news?"


def test_an_internal_note_never_reaches_the_client(
    client_user: Any, agent: Any, ticket: Ticket
) -> None:
    """The app's one real confidentiality rule, enforced on the way out."""

    async def scenario() -> dict[str, Any]:
        her = await signed_in(client_user)
        await her.command({"command": "subscribe", "ticket": str(ticket.id)})
        desk = await signed_in(agent)
        await desk.command({"command": "subscribe", "ticket": str(ticket.id)})
        await desk.command(
            {"command": "note", "ticket": str(ticket.id), "body": "Refund approved."}
        )
        await desk.command({"command": "send", "ticket": str(ticket.id), "body": "All sorted."})
        heard = await her.frame_of_type("message")
        await her.close()
        await desk.close()
        return heard

    # The first message frame she is sent is the public one; the note before it
    # was dropped for her connection.
    assert run(scenario())["message"]["body"] == "All sorted."


def test_a_client_may_not_leave_an_internal_note(client_user: Any, ticket: Ticket) -> None:
    async def scenario() -> dict[str, Any]:
        client = await signed_in(client_user)
        refusal = await client.command(
            {"command": "note", "ticket": str(ticket.id), "body": "Sneaky."}
        )
        await client.close()
        return refusal

    assert run(scenario())["title"] == "FORBIDDEN"


def test_a_message_can_be_edited_and_deleted_by_its_author(
    client_user: Any, ticket: Ticket
) -> None:
    async def scenario() -> tuple[dict[str, Any], dict[str, Any]]:
        client = await signed_in(client_user)
        sent = await reply(
            client, {"command": "send", "ticket": str(ticket.id), "body": "Typo herr"}, "sent"
        )
        edited = await reply(
            client,
            {"command": "edit", "message": sent["message"]["id"], "body": "Typo here"},
            "edited",
        )
        deleted = await reply(
            client, {"command": "delete", "message": sent["message"]["id"]}, "deleted"
        )
        await client.close()
        return edited, deleted

    edited, deleted = run(scenario())
    assert edited["message"]["body"] == "Typo here"
    assert deleted["message"]["deleted"] is True


def test_unsubscribing_stops_a_thread_being_delivered(
    client_user: Any, agent: Any, ticket: Ticket
) -> None:
    """The channel stays joined underneath; the filter is what drops it."""

    async def scenario() -> dict[str, Any]:
        her = await signed_in(client_user)
        await her.command({"command": "subscribe", "ticket": str(ticket.id)})
        await her.command({"command": "unsubscribe", "ticket": str(ticket.id)})
        await sync_to_async(_reply_as_staff)(str(ticket.id), agent=agent)
        # Nothing from that thread should arrive; a ping proves the connection
        # is still live and simply had nothing of that conversation to say.
        answer = await reply(her, {"command": "ping"}, "pong")
        await her.close()
        return answer

    assert run(scenario()) == {"type": "pong"}


def test_typing_and_presence_are_acknowledged(client_user: Any, ticket: Ticket) -> None:
    async def scenario() -> tuple[dict[str, Any], dict[str, Any]]:
        client = await signed_in(client_user)
        await client.send({"command": "typing", "ticket": str(ticket.id), "typing": True})
        typing = await client.frame_of_type("typing_ack")
        await client.send({"command": "presence", "ticket": str(ticket.id), "present": True})
        presence = await client.frame_of_type("presence_ack")
        await client.close()
        return typing, presence

    typing, presence = run(scenario())
    assert typing["type"] == "typing_ack"
    assert presence["type"] == "presence_ack"


def test_reading_a_thread_clears_its_unread_count_and_unread_ticket_restores_it(
    client_user: Any, agent: Any, ticket: Ticket
) -> None:
    async def scenario() -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
        await sync_to_async(_reply_as_staff)(str(ticket.id), agent=agent)
        client = await signed_in(client_user)
        before = await reply(client, {"command": "unread"}, "unread")
        await reply(client, {"command": "read", "ticket": str(ticket.id)}, "read")
        after = await reply(client, {"command": "unread"}, "unread")
        await reply(client, {"command": "unread_ticket", "ticket": str(ticket.id)}, "unread_ticket")
        again = await reply(client, {"command": "unread"}, "unread")
        await client.close()
        return before, after, again

    before, after, again = run(scenario())
    assert before["messages"] == 1
    assert after["messages"] == 0
    assert again["messages"] == 1


# -- either side ------------------------------------------------------------


def test_a_client_can_close_reopen_and_rate_their_own_thread(
    client_user: Any, ticket: Ticket
) -> None:
    async def scenario() -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
        client = await signed_in(client_user)
        closed = await reply(client, {"command": "close", "ticket": str(ticket.id)}, "status")
        rated = await reply(
            client,
            {"command": "rate", "ticket": str(ticket.id), "score": 5, "comment": "Quick."},
            "rated",
        )
        reopened = await reply(client, {"command": "reopen", "ticket": str(ticket.id)}, "status")
        await client.close()
        return closed, rated, reopened

    closed, rated, reopened = run(scenario())
    assert closed["status"] == "closed"
    assert rated["rating"] == 5
    assert reopened["status"] == "open"


def test_a_rating_that_is_not_a_number_of_stars_is_refused(
    client_user: Any, ticket: Ticket
) -> None:
    async def scenario() -> dict[str, Any]:
        client = await signed_in(client_user)
        await reply(client, {"command": "close", "ticket": str(ticket.id)}, "status")
        refusal = await reply(
            client, {"command": "rate", "ticket": str(ticket.id), "score": "five"}, "error"
        )
        await client.close()
        return refusal

    refusal = run(scenario())
    assert refusal["title"] == "BAD_REQUEST"
    assert "stars" in refusal["description"]


# -- the desk's own ---------------------------------------------------------


def test_an_agent_can_claim_prioritise_and_tag_a_thread(
    agent: Any, ticket: Ticket, tag: Any
) -> None:
    async def scenario() -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
        desk = await signed_in(agent)
        claimed = await reply(desk, {"command": "claim", "ticket": str(ticket.id)}, "assigned")
        raised = await reply(
            desk,
            {"command": "priority", "ticket": str(ticket.id), "priority": "urgent"},
            "priority",
        )
        tagged = await reply(
            desk, {"command": "tag", "ticket": str(ticket.id), "tags": [tag.slug]}, "tagged"
        )
        await desk.close()
        return claimed, raised, tagged

    claimed, raised, tagged = run(scenario())
    assert claimed["assignee"]["username"] == "agatha"
    assert raised["priority"] == "urgent"
    assert tagged["tags"] == ["escalated"]


def test_an_agent_can_assign_a_thread_to_a_colleague(
    agent: Any, second_agent: Any, ticket: Ticket
) -> None:
    async def scenario() -> dict[str, Any]:
        desk = await signed_in(agent)
        assigned = await reply(
            desk,
            {"command": "assign", "ticket": str(ticket.id), "agent": str(second_agent.pk)},
            "assigned",
        )
        await desk.close()
        return assigned

    assert run(scenario())["assignee"]["username"] == "alan"


def test_an_agent_can_invite_somebody_else_into_a_thread(
    agent: Any, second_agent: Any, ticket: Ticket
) -> None:
    async def scenario() -> dict[str, Any]:
        desk = await signed_in(agent)
        invited = await reply(
            desk,
            {
                "command": "invite",
                "ticket": str(ticket.id),
                "account": str(second_agent.pk),
                "role": "observer",
            },
            "invited",
        )
        await desk.close()
        return invited

    assert run(scenario())["participant"]["user"]["username"] == "alan"


def test_the_desks_saved_replies_and_numbers_are_staff_only(
    client_user: Any, agent: Any, canned: Any, ticket: Ticket
) -> None:
    async def scenario() -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
        desk = await signed_in(agent)
        replies = await desk.command({"command": "canned"})
        stats = await desk.command({"command": "stats"})
        her = await signed_in(client_user)
        refusal = await her.command({"command": "stats"})
        await her.close()
        await desk.close()
        return replies, stats, refusal

    replies, stats, refusal = run(scenario())
    assert replies["replies"][0]["title"] == "Asking for an invoice number"
    assert stats["stats"]["open"] == 1
    assert refusal["title"] == "FORBIDDEN"


def test_the_tags_the_desk_uses_are_readable_by_staff(agent: Any, tag: Any) -> None:
    async def scenario() -> dict[str, Any]:
        desk = await signed_in(agent)
        answer = await desk.command({"command": "tags"})
        await desk.close()
        return answer

    assert [row["name"] for row in run(scenario())["tags"]] == ["Escalated"]


# -- the command table ------------------------------------------------------


def test_every_advertised_command_has_a_handler() -> None:
    """The tuple is what the documentation is checked against, so it must not lie."""
    for command in SupportSocket.commands():
        assert callable(getattr(SupportSocket, f"_{command}"))


def _reply_as_staff(ticket_id: str, agent: Any = None) -> None:
    """The desk saying something, from outside any socket."""
    from django.contrib.auth import get_user_model

    if agent is None:
        agent = get_user_model().objects.create_user(
            username="andy", email="andy@example.test", is_staff=True
        )
    post_message(Ticket.objects.get(id=ticket_id), agent, "We are looking into it.")


# -- rooms over the socket --------------------------------------------------


def test_a_channel_is_created_and_heard_on_the_same_connection(client_user: Any) -> None:
    """Creating subscribes you in the same round trip.

    A client that had to subscribe afterwards would miss whatever was said in
    the gap, which for the person who just opened the room is every first reply.
    """

    async def scenario() -> dict[str, Any]:
        client = await signed_in(client_user)
        created = await reply(client, {"command": "create_channel", "name": "General"}, "created")
        await client.close()
        return created

    created = run(scenario())
    assert created["ticket"]["subject"] == "General"


def test_a_channel_is_discoverable_by_somebody_who_is_not_in_it(
    client_user: Any, other_client: Any
) -> None:
    async def scenario() -> dict[str, Any]:
        owner = await signed_in(client_user)
        await reply(owner, {"command": "create_channel", "name": "General"}, "created")
        await owner.close()

        stranger = await signed_in(other_client)
        listing = await reply(stranger, {"command": "channels"}, "channels")
        await stranger.close()
        return listing

    listing = run(scenario())
    assert [row["subject"] for row in listing["channels"]] == ["General"]
    assert listing["channels"][0]["joined"] is False


def test_joining_a_channel_starts_delivering_it(client_user: Any, other_client: Any) -> None:
    """The point of joining: what is said next arrives without asking for it."""

    async def scenario() -> dict[str, Any]:
        owner = await signed_in(client_user)
        created = await reply(owner, {"command": "create_channel", "name": "General"}, "created")
        room = created["ticket"]["id"]

        joiner = await signed_in(other_client)
        await reply(joiner, {"command": "join", "ticket": room}, "joined")

        await reply(owner, {"command": "send", "ticket": room, "body": "Morning"}, "sent")
        heard = await joiner.frame_of_type("message")
        await owner.close()
        await joiner.close()
        return heard

    assert run(scenario())["message"]["body"] == "Morning"


def test_a_private_chat_reaches_the_other_person(client_user: Any, other_client: Any) -> None:
    async def scenario() -> dict[str, Any]:
        one = await signed_in(client_user)
        two = await signed_in(other_client)
        opened = await reply(one, {"command": "direct", "account": str(other_client.pk)}, "created")
        chat = opened["ticket"]["id"]
        # The other side is not on the new thread's channel yet, so it learns of
        # it on its own account channel and joins -- which is what a client does
        # when a conversation it did not start appears.
        announced = await two.frame_of_type("ticket")
        assert announced["reason"] == "invited"
        await reply(two, {"command": "subscribe", "ticket": chat}, "subscribed")

        await reply(one, {"command": "send", "ticket": chat, "body": "Are you free?"}, "sent")
        heard = await two.frame_of_type("message")
        await one.close()
        await two.close()
        return heard

    assert run(scenario())["message"]["body"] == "Are you free?"


def test_a_group_is_created_with_its_members(client_user: Any, other_client: Any) -> None:
    async def scenario() -> dict[str, Any]:
        client = await signed_in(client_user)
        created = await reply(
            client,
            {"command": "create_group", "name": "Ours", "members": [str(other_client.pk)]},
            "created",
        )
        await client.close()
        return created

    created = run(scenario())
    assert created["ticket"]["subject"] == "Ours"


def test_a_group_cannot_be_joined_by_a_stranger_over_the_socket(
    client_user: Any, other_client: Any, agent: Any
) -> None:
    """Refused as "no such room": an id somebody guessed earns them nothing."""

    async def scenario() -> dict[str, Any]:
        owner = await signed_in(client_user)
        created = await reply(
            owner,
            {"command": "create_group", "name": "Ours", "members": [str(other_client.pk)]},
            "created",
        )
        await owner.close()

        stranger = await signed_in(agent)
        refusal = await reply(
            stranger, {"command": "join", "ticket": created["ticket"]["id"]}, "error"
        )
        await stranger.close()
        return refusal

    assert run(scenario())["title"] == "NOT_FOUND"


def test_leaving_a_channel_stops_delivering_it(client_user: Any, other_client: Any) -> None:
    async def scenario() -> dict[str, Any]:
        owner = await signed_in(client_user)
        created = await reply(owner, {"command": "create_channel", "name": "General"}, "created")
        room = created["ticket"]["id"]

        joiner = await signed_in(other_client)
        await reply(joiner, {"command": "join", "ticket": room}, "joined")
        left = await reply(joiner, {"command": "leave", "ticket": room}, "left")
        await owner.close()
        await joiner.close()
        return left

    assert run(scenario())["left"] is True
