"""The service layer: what a client may do, and what only the desk may do.

Every transport calls this, so these are the tests that stand behind all four.
The permission assertions are the important half -- a rule that holds here holds
over HTTP, GraphQL, gRPC and the socket, and a rule that does not hold here
holds nowhere.
"""

from typing import Any
from uuid import uuid4

import pytest
from django.utils import timezone

from apps.support.models import (
    Kind,
    MessageKind,
    Priority,
    Role,
    Status,
    Ticket,
    Upload,
    join,
    post_message,
)
from apps.support.services import (
    InvalidRequest,
    MessageNotFound,
    NotPermitted,
    TicketNotFound,
    support_service,
)

pytestmark = pytest.mark.django_db


# -- opening ---------------------------------------------------------------


def test_a_client_opens_a_ticket_with_its_first_message(client_user: Any, category: Any) -> None:
    payload = support_service.open(
        client_user,
        subject="I was charged twice",
        body="Two charges on the 3rd.",
        category="billing",
    )

    assert payload["subject"] == "I was charged twice"
    assert payload["status"] == Status.OPEN
    assert payload["category"]["slug"] == "billing"
    assert payload["reference"].startswith("SUP-")
    page = support_service.messages(client_user, payload["id"])
    assert [message["body"] for message in page["messages"]] == ["Two charges on the 3rd."]


def test_a_ticket_needs_a_subject_and_a_body(client_user: Any) -> None:
    with pytest.raises(InvalidRequest):
        support_service.open(client_user, subject="", body="Something")
    with pytest.raises(InvalidRequest):
        support_service.open(client_user, subject="Something", body="")


def test_a_chat_needs_neither(client_user: Any) -> None:
    """A widget opens the conversation before the visitor has typed anything."""
    payload = support_service.open(client_user, kind=str(Kind.CHAT))

    assert payload["kind"] == Kind.CHAT
    assert support_service.messages(client_user, payload["id"])["messages"] == []


def test_opening_under_a_category_that_does_not_exist_is_refused(
    client_user: Any,
) -> None:
    with pytest.raises(InvalidRequest):
        support_service.open(client_user, subject="X", body="Y", category="nonsense")


def test_opening_under_a_retired_category_is_refused(client_user: Any, category: Any) -> None:
    """Retiring a category stops new filings without touching the old ones."""
    category.is_active = False
    category.save()

    with pytest.raises(InvalidRequest):
        support_service.open(client_user, subject="X", body="Y", category="billing")


def test_an_unknown_kind_is_refused_by_name(client_user: Any) -> None:
    with pytest.raises(InvalidRequest, match="chat"):
        support_service.open(client_user, kind="urgent-thing")


# -- who sees what ---------------------------------------------------------


def test_one_client_cannot_read_another_clients_ticket(other_client: Any, ticket: Ticket) -> None:
    with pytest.raises(TicketNotFound):
        support_service.ticket(other_client, ticket.pk)


def test_a_ticket_that_does_not_exist_answers_the_same_way(client_user: Any) -> None:
    """The two are one answer, so an id cannot be used to learn what exists."""
    with pytest.raises(TicketNotFound):
        support_service.ticket(client_user, uuid4())


def test_staff_read_any_ticket(agent: Any, ticket: Ticket) -> None:
    assert support_service.ticket(agent, ticket.pk)["reference"] == ticket.reference


def test_a_client_does_not_get_the_internal_notes(
    client_user: Any, agent: Any, ticket: Ticket
) -> None:
    post_message(ticket, agent, "Probably a duplicate", kind=str(MessageKind.NOTE))
    post_message(ticket, agent, "We are looking into it.")

    bodies = [
        message["body"] for message in support_service.messages(client_user, ticket.pk)["messages"]
    ]

    assert "Probably a duplicate" not in bodies
    assert "We are looking into it." in bodies


def test_staff_get_the_internal_notes(agent: Any, ticket: Ticket) -> None:
    post_message(ticket, agent, "Probably a duplicate", kind=str(MessageKind.NOTE))

    bodies = [message["body"] for message in support_service.messages(agent, ticket.pk)["messages"]]

    assert "Probably a duplicate" in bodies


def test_a_client_naming_an_internal_note_gets_the_missing_message_answer(
    client_user: Any, agent: Any, ticket: Ticket
) -> None:
    note = post_message(ticket, agent, "Internal", kind=str(MessageKind.NOTE))

    with pytest.raises(MessageNotFound):
        support_service.edit(client_user, note.pk, "Rewritten")


def test_a_client_sees_a_trimmed_participant_list(
    client_user: Any, agent: Any, second_agent: Any, ticket: Ticket
) -> None:
    """They see the desk as the agent who owns it, not as a roster of readers."""
    support_service.assign(agent, ticket.pk, agent.pk)
    join(ticket, second_agent, role=str(Role.AGENT))

    usernames = {
        person["user"]["username"]
        for person in support_service.ticket(client_user, ticket.pk)["participants"]
    }

    assert usernames == {"clara", "agatha"}
    assert {
        person["user"]["username"]
        for person in support_service.ticket(agent, ticket.pk)["participants"]
    } == {"clara", "agatha", "alan"}


# -- the queue -------------------------------------------------------------


def test_a_client_lists_only_their_own(client_user: Any, other_client: Any, ticket: Ticket) -> None:
    support_service.open(other_client, subject="Theirs", body="Theirs")

    rows = support_service.tickets(client_user)

    assert [row["reference"] for row in rows] == [ticket.reference]
    assert support_service.count(client_user) == 1


def test_staff_list_the_whole_desk(agent: Any, client_user: Any, other_client: Any) -> None:
    support_service.open(client_user, subject="One", body="One")
    support_service.open(other_client, subject="Two", body="Two")

    assert support_service.count(agent) == 2


def test_the_queue_is_ordered_by_most_recent_activity(agent: Any, client_user: Any) -> None:
    first = support_service.open(client_user, subject="First", body="First")
    second = support_service.open(client_user, subject="Second", body="Second")
    support_service.send(agent, first["id"], "Answering the older one.")

    references = [row["reference"] for row in support_service.tickets(agent)]

    assert references[0] == first["reference"]
    assert references[1] == second["reference"]


def test_mine_means_assigned_to_me_for_an_agent(
    agent: Any, second_agent: Any, client_user: Any
) -> None:
    mine = support_service.open(client_user, subject="Mine", body="Mine")
    theirs = support_service.open(client_user, subject="Theirs", body="Theirs")
    support_service.assign(agent, mine["id"], agent.pk)
    support_service.assign(agent, theirs["id"], second_agent.pk)

    rows = support_service.tickets(agent, mine=True)

    assert [row["reference"] for row in rows] == [mine["reference"]]


def test_mine_means_opened_by_me_for_a_client(client_user: Any, ticket: Ticket) -> None:
    assert len(support_service.tickets(client_user, mine=True)) == 1


def test_a_client_cannot_read_the_unassigned_queue(client_user: Any, ticket: Ticket) -> None:
    with pytest.raises(NotPermitted):
        support_service.tickets(client_user, unassigned=True)


def test_a_client_cannot_filter_by_agent(client_user: Any, agent: Any) -> None:
    with pytest.raises(NotPermitted):
        support_service.tickets(client_user, assignee=str(agent.pk))


def test_the_unassigned_queue_is_what_nobody_has_picked_up(agent: Any, client_user: Any) -> None:
    taken = support_service.open(client_user, subject="Taken", body="Taken")
    support_service.open(client_user, subject="Free", body="Free")
    support_service.claim(agent, taken["id"])

    rows = support_service.tickets(agent, unassigned=True)

    assert [row["subject"] for row in rows] == ["Free"]


def test_live_and_settled_are_two_halves_of_the_desk(agent: Any, client_user: Any) -> None:
    settled = support_service.open(client_user, subject="Done", body="Done")
    support_service.open(client_user, subject="Ongoing", body="Ongoing")
    support_service.close(agent, settled["id"])

    assert [row["subject"] for row in support_service.tickets(agent, live=True)] == ["Ongoing"]
    assert [row["subject"] for row in support_service.tickets(agent, live=False)] == ["Done"]


def test_search_from_the_queue(agent: Any, ticket: Ticket) -> None:
    assert support_service.count(agent, search="charged twice") == 1
    assert support_service.count(agent, search="unrelated") == 0


def test_breached_filters_after_the_page(agent: Any, ticket: Ticket) -> None:
    """A breach is computed against the clock, so it cannot be a WHERE clause."""
    assert len(support_service.tickets(agent, breached=True)) == 0

    Ticket.objects.filter(pk=ticket.pk).update(
        first_response_due_at=timezone.now() - timezone.timedelta(hours=1)
    )

    assert len(support_service.tickets(agent, breached=True)) == 1
    assert len(support_service.tickets(agent, breached=False)) == 0


def test_a_page_is_capped(agent: Any, client_user: Any) -> None:
    for index in range(5):
        support_service.open(client_user, subject=f"#{index}", body="Body")

    assert len(support_service.tickets(agent, limit=2)) == 2
    assert len(support_service.tickets(agent, limit=10_000)) == 5
    assert support_service.count(agent) == 5


def test_an_unknown_status_is_refused_by_name(agent: Any) -> None:
    with pytest.raises(InvalidRequest, match="resolved"):
        support_service.tickets(agent, status="finished")


# -- talking ---------------------------------------------------------------


def test_a_client_replies_to_their_own_ticket(client_user: Any, ticket: Ticket) -> None:
    message = support_service.send(client_user, ticket.pk, "Any news?")

    assert message["body"] == "Any news?"
    assert message["author"]["username"] == "clara"
    assert message["visibility"] == "public"


def test_replying_marks_the_thread_read(client_user: Any, agent: Any, ticket: Ticket) -> None:
    """Whatever was in the thread when you replied to it, you have seen."""
    support_service.send(agent, ticket.pk, "Here is the answer.")
    assert support_service.ticket(client_user, ticket.pk)["unread"] == 1

    support_service.send(client_user, ticket.pk, "Thanks.")

    assert support_service.ticket(client_user, ticket.pk)["unread"] == 0


def test_a_client_cannot_leave_an_internal_note(client_user: Any, ticket: Ticket) -> None:
    with pytest.raises(NotPermitted):
        support_service.send(client_user, ticket.pk, "Sneaky", internal=True)


def test_an_agent_leaves_an_internal_note(agent: Any, ticket: Ticket) -> None:
    message = support_service.send(agent, ticket.pk, "Duplicate charge", internal=True)

    assert message["visibility"] == "internal"
    assert message["kind"] == MessageKind.NOTE


def test_an_empty_message_is_refused(client_user: Any, ticket: Ticket) -> None:
    with pytest.raises(InvalidRequest):
        support_service.send(client_user, ticket.pk, "   ")


def test_a_message_with_no_body_but_a_file_is_fine(client_user: Any, ticket: Ticket) -> None:
    """Sending somebody a screenshot without a covering note is normal."""
    upload = Upload.objects.create(owner=client_user, name="shot.png", url="/shot.png", size=9)

    message = support_service.send(client_user, ticket.pk, "", upload_ids=[upload.pk])

    assert message["body"] == ""
    assert [item["name"] for item in message["attachments"]] == ["shot.png"]


def test_a_closed_thread_refuses_a_message(agent: Any, ticket: Ticket) -> None:
    support_service.close(agent, ticket.pk)

    with pytest.raises(NotPermitted, match="closed"):
        support_service.send(agent, ticket.pk, "One more thing")


def test_replying_puts_an_agent_in_the_thread(agent: Any, ticket: Ticket) -> None:
    support_service.send(agent, ticket.pk, "On it.")

    assert ticket.participants.filter(user=agent, role=Role.AGENT).exists()


def test_editing_your_own_message(client_user: Any, ticket: Ticket) -> None:
    message = support_service.send(client_user, ticket.pk, "Frist")

    edited = support_service.edit(client_user, message["id"], "First")

    assert edited["body"] == "First"
    assert edited["edited_at"] is not None


def test_editing_somebody_elses_message_is_refused(
    client_user: Any, agent: Any, ticket: Ticket
) -> None:
    message = support_service.send(agent, ticket.pk, "Ours")

    with pytest.raises(NotPermitted):
        support_service.edit(client_user, message["id"], "Theirs")


def test_staff_may_retract_a_client_message(client_user: Any, agent: Any, ticket: Ticket) -> None:
    message = support_service.send(client_user, ticket.pk, "My card is 4111 1111 1111 1111")

    deleted = support_service.delete(agent, message["id"])

    assert deleted["deleted"] is True
    assert deleted["body"] == ""


def test_a_client_cannot_retract_a_staff_message(
    client_user: Any, agent: Any, ticket: Ticket
) -> None:
    message = support_service.send(agent, ticket.pk, "Our answer")

    with pytest.raises(NotPermitted):
        support_service.delete(client_user, message["id"])


# -- attachments -----------------------------------------------------------


def test_an_upload_can_only_be_claimed_once(client_user: Any, ticket: Ticket) -> None:
    upload = Upload.objects.create(owner=client_user, name="shot.png", url="/shot.png")
    support_service.send(client_user, ticket.pk, "First", upload_ids=[upload.pk])

    with pytest.raises(InvalidRequest):
        support_service.send(client_user, ticket.pk, "Again", upload_ids=[upload.pk])


def test_somebody_elses_upload_cannot_be_claimed(
    client_user: Any, other_client: Any, ticket: Ticket
) -> None:
    theirs = Upload.objects.create(owner=other_client, name="theirs.png", url="/theirs.png")

    with pytest.raises(InvalidRequest):
        support_service.send(client_user, ticket.pk, "Mine now", upload_ids=[theirs.pk])


def test_an_upload_that_does_not_exist_is_refused(client_user: Any, ticket: Ticket) -> None:
    with pytest.raises(InvalidRequest):
        support_service.send(client_user, ticket.pk, "Look", upload_ids=[uuid4()])


# -- read state ------------------------------------------------------------


def test_reading_and_unreading_a_thread(client_user: Any, agent: Any, ticket: Ticket) -> None:
    support_service.send(agent, ticket.pk, "Answer.")

    read = support_service.read(client_user, ticket.pk)
    assert read["changed"] is True
    assert read["unread"] == 0
    assert read["last_read_at"] is not None

    again = support_service.read(client_user, ticket.pk)
    assert again["changed"] is False

    back = support_service.unread_ticket(client_user, ticket.pk)
    assert back["changed"] is True
    assert back["unread"] == 1
    assert back["last_read_at"] is None


def test_the_badge_counts_messages_and_threads(
    client_user: Any, agent: Any, ticket: Ticket
) -> None:
    second = support_service.open(client_user, subject="Another", body="Another")
    support_service.send(agent, ticket.pk, "One.")
    support_service.send(agent, ticket.pk, "Two.")
    support_service.send(agent, second["id"], "Three.")

    assert support_service.unread(client_user) == {"messages": 3, "tickets": 2}


def test_the_badge_ignores_internal_notes_for_a_client(
    client_user: Any, agent: Any, ticket: Ticket
) -> None:
    support_service.send(agent, ticket.pk, "A note", internal=True)

    assert support_service.unread(client_user) == {"messages": 0, "tickets": 0}


# -- status ----------------------------------------------------------------


def test_a_client_may_close_and_reopen_their_own(client_user: Any, ticket: Ticket) -> None:
    assert support_service.close(client_user, ticket.pk)["status"] == Status.CLOSED
    assert support_service.reopen(client_user, ticket.pk)["status"] == Status.OPEN


def test_a_client_may_not_say_the_desk_is_waiting_on_them(client_user: Any, ticket: Ticket) -> None:
    with pytest.raises(NotPermitted):
        support_service.status(client_user, ticket.pk, str(Status.PENDING))
    with pytest.raises(NotPermitted):
        support_service.status(client_user, ticket.pk, str(Status.ON_HOLD))


def test_an_observer_who_is_not_the_client_may_not_settle_it(
    other_client: Any, ticket: Ticket
) -> None:
    join(ticket, other_client, role=str(Role.OBSERVER))

    with pytest.raises(NotPermitted):
        support_service.close(other_client, ticket.pk)


def test_a_status_change_is_written_into_the_thread(
    agent: Any, client_user: Any, ticket: Ticket
) -> None:
    support_service.status(agent, ticket.pk, str(Status.RESOLVED))

    events = [
        message
        for message in support_service.messages(client_user, ticket.pk)["messages"]
        if message["kind"] == MessageKind.EVENT
    ]

    assert len(events) == 1
    assert events[0]["data"] == {
        "event": "status",
        "field": "status",
        "from": "open",
        "to": "resolved",
    }


def test_setting_a_status_twice_writes_one_event(agent: Any, ticket: Ticket) -> None:
    support_service.status(agent, ticket.pk, str(Status.RESOLVED))
    second = support_service.status(agent, ticket.pk, str(Status.RESOLVED))

    assert second["changed"] is False
    events = [
        message
        for message in support_service.messages(agent, ticket.pk)["messages"]
        if message["kind"] == MessageKind.EVENT
    ]
    assert len(events) == 1


# -- assignment ------------------------------------------------------------


def test_a_client_cannot_assign(client_user: Any, agent: Any, ticket: Ticket) -> None:
    with pytest.raises(NotPermitted):
        support_service.assign(client_user, ticket.pk, agent.pk)


def test_an_agent_claims_an_unassigned_ticket(agent: Any, ticket: Ticket) -> None:
    result = support_service.claim(agent, ticket.pk)

    assert result["assignee"]["username"] == "agatha"
    assert result["changed"] is True


def test_two_agents_cannot_both_claim_it(agent: Any, second_agent: Any, ticket: Ticket) -> None:
    support_service.claim(agent, ticket.pk)

    with pytest.raises(NotPermitted, match="agatha"):
        support_service.claim(second_agent, ticket.pk)


def test_claiming_what_you_already_have_is_not_a_conflict(agent: Any, ticket: Ticket) -> None:
    support_service.claim(agent, ticket.pk)

    assert support_service.claim(agent, ticket.pk)["changed"] is False


def test_a_ticket_cannot_be_assigned_to_somebody_who_is_not_staff(
    agent: Any, other_client: Any, ticket: Ticket
) -> None:
    with pytest.raises(InvalidRequest):
        support_service.assign(agent, ticket.pk, other_client.pk)


def test_an_assignment_is_recorded_where_the_client_cannot_see_it(
    agent: Any, client_user: Any, ticket: Ticket
) -> None:
    """Who a complaint was passed between is the desk's business."""
    support_service.assign(agent, ticket.pk, agent.pk)

    client_sees = support_service.messages(client_user, ticket.pk)["messages"]
    desk_sees = support_service.messages(agent, ticket.pk)["messages"]

    assert not any(message["kind"] == MessageKind.EVENT for message in client_sees)
    assert any(message["data"].get("field") == "assignee" for message in desk_sees)


# -- priority, tags, invitations, rating -----------------------------------


def test_a_client_cannot_reprioritise(client_user: Any, ticket: Ticket) -> None:
    with pytest.raises(NotPermitted):
        support_service.priority(client_user, ticket.pk, str(Priority.URGENT))


def test_an_agent_reprioritises(agent: Any, ticket: Ticket) -> None:
    assert support_service.priority(agent, ticket.pk, str(Priority.URGENT))["priority"] == "urgent"


def test_tags_are_replaced_rather_than_added(agent: Any, ticket: Ticket, tag: Any) -> None:
    from apps.support.models import Tag

    Tag.objects.create(name="Refund")

    assert support_service.tag(agent, ticket.pk, ["escalated"])["tags"] == ["escalated"]
    assert support_service.tag(agent, ticket.pk, ["refund"])["tags"] == ["refund"]


def test_an_unknown_tag_is_refused_by_name(agent: Any, ticket: Ticket) -> None:
    with pytest.raises(InvalidRequest, match="nonsense"):
        support_service.tag(agent, ticket.pk, ["nonsense"])


def test_a_client_cannot_read_the_tag_list(client_user: Any, tag: Any) -> None:
    with pytest.raises(NotPermitted):
        support_service.tags(client_user)


def test_an_agent_invites_a_colleague(agent: Any, second_agent: Any, ticket: Ticket) -> None:
    participant = support_service.invite(agent, ticket.pk, second_agent.pk, str(Role.AGENT))

    assert participant["user"]["username"] == "alan"
    assert participant["role"] == Role.AGENT


def test_a_client_cannot_invite_anybody(
    client_user: Any, other_client: Any, ticket: Ticket
) -> None:
    with pytest.raises(NotPermitted):
        support_service.invite(client_user, ticket.pk, other_client.pk)


def test_an_invited_observer_who_is_not_staff_reads_the_public_half(
    agent: Any, other_client: Any, client_user: Any, ticket: Ticket
) -> None:
    support_service.invite(agent, ticket.pk, other_client.pk, str(Role.OBSERVER))
    support_service.send(agent, ticket.pk, "A note", internal=True)
    support_service.send(agent, ticket.pk, "A reply")

    bodies = [
        message["body"] for message in support_service.messages(other_client, ticket.pk)["messages"]
    ]

    assert "A note" not in bodies
    assert "A reply" in bodies


def test_only_the_client_rates_and_only_once_settled(
    client_user: Any, agent: Any, ticket: Ticket
) -> None:
    with pytest.raises(NotPermitted, match="resolved or closed"):
        support_service.rate(client_user, ticket.pk, 5)

    support_service.close(agent, ticket.pk)

    with pytest.raises(NotPermitted, match="client who opened"):
        support_service.rate(agent, ticket.pk, 5)

    assert support_service.rate(client_user, ticket.pk, 5, "Great")["rating"] == 5


def test_a_rating_outside_one_to_five_is_refused(
    client_user: Any, agent: Any, ticket: Ticket
) -> None:
    support_service.close(agent, ticket.pk)

    for score in (0, 6, True, 2.5):
        with pytest.raises(InvalidRequest):
            support_service.rate(client_user, ticket.pk, score)  # type: ignore[arg-type]


# -- the desk's own reading ------------------------------------------------


def test_categories_are_readable_by_a_client(client_user: Any, category: Any) -> None:
    rows = support_service.categories()

    assert [row["slug"] for row in rows] == ["billing"]
    assert rows[0]["first_response_minutes"] == 60


def test_a_retired_category_is_not_offered(category: Any) -> None:
    category.is_active = False
    category.save()

    assert support_service.categories() == []


def test_a_client_cannot_read_the_canned_replies(client_user: Any, canned: Any) -> None:
    with pytest.raises(NotPermitted):
        support_service.canned_replies(client_user)


def test_fetching_a_canned_reply_counts_it(agent: Any, canned: Any) -> None:
    """The count is what tells a desk which of its saved replies are dead weight."""
    support_service.canned_replies(agent)
    support_service.canned_replies(agent)
    canned.refresh_from_db()

    assert canned.used_count == 2


def test_canned_replies_can_be_narrowed_to_a_category(
    agent: Any, canned: Any, category: Any
) -> None:
    from apps.support.models import CannedReply

    CannedReply.objects.create(title="Billing only", body="...", category=category)

    titles = {row["title"] for row in support_service.canned_replies(agent, category="billing")}

    # The uncategorised one is offered everywhere, so it is here too.
    assert titles == {"Asking for an invoice number", "Billing only"}


def test_a_client_cannot_read_the_desk_statistics(client_user: Any, ticket: Ticket) -> None:
    with pytest.raises(NotPermitted):
        support_service.stats(client_user)


def test_the_desk_statistics_count_what_is_there(
    agent: Any, client_user: Any, ticket: Ticket
) -> None:
    support_service.open(client_user, kind=str(Kind.CHAT))
    settled = support_service.open(client_user, subject="Done", body="Done")
    support_service.close(agent, settled["id"])
    support_service.rate(client_user, settled["id"], 4)

    stats = support_service.stats(agent)

    assert stats["total"] == 3
    assert stats["closed"] == 1
    assert stats["chats"] == 1
    assert stats["rated"] == 1
    assert stats["satisfaction"] == 4.0
    assert stats["unassigned"] == 2
    assert stats["awaiting_first_response"] == 2


def test_satisfaction_is_null_before_anybody_has_rated(agent: Any, ticket: Ticket) -> None:
    assert support_service.stats(agent)["satisfaction"] is None


# -- ephemeral -------------------------------------------------------------


def test_typing_and_presence_are_scoped_to_a_thread_you_can_see(
    other_client: Any, ticket: Ticket
) -> None:
    with pytest.raises(TicketNotFound):
        support_service.typing(other_client, ticket.pk)
    with pytest.raises(TicketNotFound):
        support_service.presence(other_client, ticket.pk)


def test_typing_reports_what_it_published(client_user: Any, ticket: Ticket) -> None:
    assert support_service.typing(client_user, ticket.pk) == {
        "ticket": str(ticket.pk),
        "typing": True,
    }
    assert support_service.presence(client_user, ticket.pk, present=False) == {
        "ticket": str(ticket.pk),
        "present": False,
    }
