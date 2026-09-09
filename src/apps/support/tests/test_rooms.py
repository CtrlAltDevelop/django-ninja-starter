"""Channels, groups and private chats: the three kinds nobody at the desk owns.

The rules these assert are the reason the room kinds exist as their own family
rather than as another value of ``kind``. A support agent may read every ticket
in the building, and that is the job. The moment the same app carries a private
message between two customers, ``is_staff`` stops being a licence to read and
starts being the thing most likely to leak -- so the tests that matter most here
are the ones where staff are told no.
"""

from typing import Any

import pytest

from apps.support.models import Kind, Role, Ticket
from apps.support.services import InvalidRequest, NotPermitted, TicketNotFound, support_service

pytestmark = pytest.mark.django_db


# -- channels ---------------------------------------------------------------


def test_a_channel_is_open_to_anybody_signed_in(client_user: Any, other_client: Any) -> None:
    """Discovery is the point: a channel nobody can find is one nobody can join."""
    support_service.create_channel(client_user, "General")

    found = support_service.channels(other_client)

    assert [channel["subject"] for channel in found] == ["General"]
    assert found[0]["joined"] is False


def test_joining_a_channel_is_idempotent(client_user: Any, other_client: Any) -> None:
    """A client that does not track its own membership may call this every time."""
    channel = support_service.create_channel(client_user, "General")

    support_service.join_room(other_client, channel["id"])
    support_service.join_room(other_client, channel["id"])

    assert Ticket.objects.get(pk=channel["id"]).participants.count() == 2


def test_a_channel_address_is_refused_rather_than_suffixed(client_user: Any) -> None:
    """Somebody who asked for `general` and silently got `general-2` has a different channel."""
    support_service.create_channel(client_user, "General")

    with pytest.raises(InvalidRequest, match="already a channel"):
        support_service.create_channel(client_user, "General")


def test_a_channel_takes_its_address_from_its_name(client_user: Any) -> None:
    channel = support_service.create_channel(client_user, "Release Planning")

    assert Ticket.objects.get(pk=channel["id"]).slug == "release-planning"


def test_the_opener_of_a_channel_owns_it(client_user: Any) -> None:
    channel = support_service.create_channel(client_user, "General")

    membership = Ticket.objects.get(pk=channel["id"]).participants.get(user=client_user)
    assert membership.role == Role.OWNER


def test_leaving_a_channel_removes_the_membership(client_user: Any, other_client: Any) -> None:
    channel = support_service.create_channel(client_user, "General")
    support_service.join_room(other_client, channel["id"])

    support_service.leave_room(other_client, channel["id"])

    assert not Ticket.objects.get(pk=channel["id"]).participants.filter(user=other_client).exists()


def test_leaving_a_room_you_are_not_in_is_refused(client_user: Any, other_client: Any) -> None:
    channel = support_service.create_channel(client_user, "General")

    with pytest.raises(InvalidRequest, match="not in that room"):
        support_service.leave_room(other_client, channel["id"])


# -- groups -----------------------------------------------------------------


def test_a_group_is_invisible_to_everybody_outside_it(
    client_user: Any, other_client: Any, agent: Any
) -> None:
    """Not listed, not discoverable, and not readable by an id somebody guessed."""
    group = support_service.create_group(client_user, "Weekend plans", [other_client.pk])

    assert group["id"] not in {channel["id"] for channel in support_service.channels(agent)}
    with pytest.raises(TicketNotFound):
        support_service.ticket(agent, group["id"])


def test_a_group_needs_somebody_else_in_it(client_user: Any) -> None:
    with pytest.raises(InvalidRequest, match="besides you"):
        support_service.create_group(client_user, "Just me", [])


def test_naming_yourself_as_a_member_is_not_an_error(client_user: Any, other_client: Any) -> None:
    """A reasonable thing for a client to send and a silly thing to fail over."""
    group = support_service.create_group(client_user, "Ours", [client_user.pk, other_client.pk])

    assert Ticket.objects.get(pk=group["id"]).participants.count() == 2


def test_a_group_cannot_be_joined_uninvited(
    client_user: Any, other_client: Any, agent: Any
) -> None:
    group = support_service.create_group(client_user, "Ours", [other_client.pk])

    with pytest.raises(TicketNotFound):
        support_service.join_room(agent, group["id"])


def test_a_member_of_a_group_can_read_it(client_user: Any, other_client: Any) -> None:
    group = support_service.create_group(client_user, "Ours", [other_client.pk])

    assert support_service.ticket(other_client, group["id"])["id"] == group["id"]


# -- private chats ----------------------------------------------------------


def test_opening_the_same_private_chat_twice_finds_the_first(
    client_user: Any, other_client: Any
) -> None:
    """Both people calling this at once must land in one conversation, not two halves."""
    first = support_service.direct(client_user, other_client.pk)
    second = support_service.direct(client_user, other_client.pk)

    assert first["id"] == second["id"]


def test_either_side_opening_it_finds_the_same_thread(client_user: Any, other_client: Any) -> None:
    """The dedupe key is sorted, so it does not matter who asked first."""
    theirs = support_service.direct(client_user, other_client.pk)
    ours = support_service.direct(other_client, client_user.pk)

    assert theirs["id"] == ours["id"]
    assert Ticket.objects.filter(kind=Kind.DIRECT).count() == 1


def test_a_private_chat_with_yourself_is_refused(client_user: Any) -> None:
    with pytest.raises(InvalidRequest, match="with yourself"):
        support_service.direct(client_user, client_user.pk)


def test_a_private_chat_with_nobody_is_refused(client_user: Any) -> None:
    from uuid import uuid4

    with pytest.raises(InvalidRequest, match="No such active account"):
        support_service.direct(client_user, uuid4())


def test_a_private_chat_cannot_be_left(client_user: Any, other_client: Any) -> None:
    """Half a private conversation is a state neither person can reason about."""
    chat = support_service.direct(client_user, other_client.pk)

    with pytest.raises(TicketNotFound):
        support_service.leave_room(client_user, chat["id"])


# -- the rule that matters --------------------------------------------------


def test_staff_cannot_read_a_private_chat_they_are_not_in(
    client_user: Any, other_client: Any, agent: Any
) -> None:
    """`is_staff` is not a warrant.

    A desk that could read its customers' private conversations would be a
    surveillance tool with a help widget attached. This is the single most
    important assertion in the app.
    """
    chat = support_service.direct(client_user, other_client.pk)

    with pytest.raises(TicketNotFound):
        support_service.ticket(agent, chat["id"])


def test_staff_cannot_read_a_group_they_are_not_in(
    client_user: Any, other_client: Any, agent: Any
) -> None:
    group = support_service.create_group(client_user, "Ours", [other_client.pk])

    with pytest.raises(TicketNotFound):
        support_service.messages(agent, group["id"])


def test_a_private_chat_never_appears_in_the_desk_queue(
    client_user: Any, other_client: Any, agent: Any
) -> None:
    """The queue is the desk's work, and somebody's private chat is not work."""
    support_service.direct(client_user, other_client.pk)
    support_service.create_group(client_user, "Ours", [other_client.pk])

    assert support_service.tickets(agent) == []


def test_staff_still_see_every_ticket(client_user: Any, agent: Any) -> None:
    """The room rules must not have narrowed what the desk is for."""
    support_service.open(client_user, subject="Charged twice", body="Two charges.")

    assert len(support_service.tickets(agent)) == 1


# -- the desk's verbs do not reach into rooms -------------------------------


def test_a_room_cannot_be_opened_through_the_ticket_path(client_user: Any) -> None:
    """Each room kind has rules `open` does not apply, so it refuses them all."""
    for kind in ("channel", "group", "direct"):
        with pytest.raises(InvalidRequest, match="own command"):
            support_service.open(client_user, kind=kind, subject="x", body="x")


def test_a_desk_thread_is_not_a_room(client_user: Any) -> None:
    """Answered as "no such room" rather than as a refusal.

    Telling somebody the id they guessed is a real ticket is itself an answer
    they had not earned.
    """
    ticket = support_service.open(client_user, subject="Charged twice", body="Two charges.")

    with pytest.raises(TicketNotFound, match="No such room"):
        support_service.join_room(client_user, ticket["id"])


def test_an_agent_cannot_claim_a_channel(client_user: Any, agent: Any) -> None:
    """A channel is not queue work and must never land in somebody's queue."""
    channel = support_service.create_channel(client_user, "General")

    with pytest.raises((NotPermitted, InvalidRequest, TicketNotFound)):
        support_service.claim(agent, channel["id"])
