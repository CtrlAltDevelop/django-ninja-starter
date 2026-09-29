"""The domain: references, deadlines, read watermarks and what a message moves.

These are the tests that matter most, because everything above them -- four
transports and an admin -- is a translation of what is asserted here.
"""

from typing import Any

import pytest
from django.core.exceptions import ValidationError
from django.db.utils import IntegrityError
from django.utils import timezone

from apps.support.models import (
    Kind,
    Message,
    MessageKind,
    Participant,
    Priority,
    Role,
    Status,
    Ticket,
    Visibility,
    assign,
    create_ticket,
    delete_message,
    edit_message,
    join,
    mark_read,
    mark_unread,
    post_message,
    prune,
    prune_uploads,
    rate,
    set_priority,
    set_status,
    total_unread,
    unread_count,
)

pytestmark = pytest.mark.django_db


# -- opening ---------------------------------------------------------------


def test_opening_a_ticket_gives_it_a_reference_and_puts_the_client_in_it(
    client_user: Any, category: Any
) -> None:
    ticket = create_ticket(client_user, subject="Hello", category=category)

    assert ticket.reference.startswith("SUP-")
    assert len(ticket.reference) == len("SUP-") + 6
    assert ticket.participants.get().user == client_user
    assert ticket.participants.get().role == Role.CLIENT


def test_two_tickets_never_share_a_reference(client_user: Any) -> None:
    references = {create_ticket(client_user, subject=f"#{n}").reference for n in range(25)}

    assert len(references) == 25


def test_a_ticket_takes_its_priority_from_its_category(client_user: Any, category: Any) -> None:
    category.default_priority = Priority.HIGH
    category.save()

    assert create_ticket(client_user, category=category).priority == Priority.HIGH


def test_an_explicit_priority_beats_the_category(client_user: Any, category: Any) -> None:
    category.default_priority = Priority.HIGH
    category.save()

    ticket = create_ticket(client_user, category=category, priority=str(Priority.LOW))

    assert ticket.priority == Priority.LOW


def test_a_category_slug_is_derived_from_its_name(category: Any) -> None:
    assert category.slug == "billing"


# -- the service level -----------------------------------------------------


def test_deadlines_are_copied_from_the_category_when_the_ticket_is_opened(
    client_user: Any, category: Any
) -> None:
    ticket = create_ticket(client_user, category=category)

    assert ticket.first_response_due_at is not None
    assert ticket.resolution_due_at is not None
    minutes = (ticket.first_response_due_at - ticket.created_at).total_seconds() / 60
    assert round(minutes) == 60


def test_editing_a_category_does_not_move_an_existing_ticket_deadline(
    client_user: Any, category: Any
) -> None:
    """The promise a ticket was opened under is the promise it is held to."""
    ticket = create_ticket(client_user, category=category)
    was = ticket.first_response_due_at

    category.first_response_minutes = 5
    category.save()
    ticket.refresh_from_db()

    assert ticket.first_response_due_at == was


def test_a_category_that_promises_nothing_gives_a_ticket_no_deadlines(
    client_user: Any, slow_category: Any
) -> None:
    ticket = create_ticket(client_user, category=slow_category)

    assert ticket.first_response_due_at is None
    assert ticket.resolution_due_at is None
    assert ticket.breached is False


def test_a_ticket_with_no_category_has_no_deadlines(client_user: Any) -> None:
    assert create_ticket(client_user, kind=str(Kind.CHAT)).breached is False


def test_a_missed_first_response_is_a_breach_the_moment_the_clock_passes(
    client_user: Any, category: Any
) -> None:
    ticket = create_ticket(client_user, category=category)
    assert ticket.first_response_breached is False

    # No job has run and nothing has been written: the deadline is simply in
    # the past now, which is the whole point of storing a deadline rather than
    # a flag.
    ticket.first_response_due_at = timezone.now() - timezone.timedelta(minutes=1)
    ticket.save()

    assert ticket.first_response_breached is True
    assert ticket.breached is True


def test_a_breach_is_measured_against_the_answer_once_there_is_one(
    client_user: Any, agent: Any, category: Any
) -> None:
    """Otherwise a thread answered an hour late would look worse every day after."""
    ticket = create_ticket(client_user, category=category)
    ticket.first_response_due_at = timezone.now() - timezone.timedelta(hours=2)
    ticket.save()
    post_message(ticket, agent, "Looking into it.")
    ticket.refresh_from_db()

    assert ticket.first_response_at is not None
    assert ticket.first_response_breached is True
    assert ticket.first_response_at > ticket.first_response_due_at


def test_answering_in_time_is_not_a_breach(client_user: Any, agent: Any, category: Any) -> None:
    ticket = create_ticket(client_user, category=category)
    post_message(ticket, agent, "On it.")
    ticket.refresh_from_db()

    assert ticket.first_response_breached is False
    assert ticket.breached is False


# -- what a message moves --------------------------------------------------


def test_a_staff_reply_stops_the_first_response_clock(
    client_user: Any, agent: Any, ticket: Ticket
) -> None:
    assert ticket.first_response_at is None

    post_message(ticket, agent, "Thanks, checking now.")
    ticket.refresh_from_db()

    assert ticket.first_response_at is not None


def test_a_client_message_does_not_stop_the_first_response_clock(
    client_user: Any, ticket: Ticket
) -> None:
    post_message(ticket, client_user, "Any news?")
    ticket.refresh_from_db()

    assert ticket.first_response_at is None


def test_an_internal_note_is_not_a_first_response(agent: Any, ticket: Ticket) -> None:
    """A note to a colleague is not an answer to the client."""
    post_message(ticket, agent, "Looks like a duplicate charge.", kind=str(MessageKind.NOTE))
    ticket.refresh_from_db()

    assert ticket.first_response_at is None
    assert ticket.status == Status.OPEN


def test_a_staff_reply_moves_the_ticket_to_waiting_on_the_client(
    agent: Any, ticket: Ticket
) -> None:
    post_message(ticket, agent, "Could you send the invoice number?")
    ticket.refresh_from_db()

    assert ticket.status == Status.PENDING


def test_a_client_reply_moves_it_back_to_open(client_user: Any, agent: Any, ticket: Ticket) -> None:
    post_message(ticket, agent, "Could you send the invoice number?")
    ticket.refresh_from_db()

    post_message(ticket, client_user, "Here it is: 1234.")
    ticket.refresh_from_db()

    assert ticket.status == Status.OPEN


def test_a_client_reply_reopens_a_resolved_ticket(
    client_user: Any, agent: Any, ticket: Ticket
) -> None:
    """Resolved is the desk's opinion. The client is entitled to disagree."""
    set_status(ticket, str(Status.RESOLVED))
    assert ticket.resolved_at is not None

    post_message(ticket, client_user, "It has happened again.")
    ticket.refresh_from_db()

    assert ticket.status == Status.OPEN
    assert ticket.resolved_at is None


def test_a_client_reply_does_not_reopen_a_closed_ticket(client_user: Any, ticket: Ticket) -> None:
    """Closing is the one final state; reopening it is an explicit request."""
    set_status(ticket, str(Status.CLOSED))

    post_message(ticket, client_user, "Hello?")
    ticket.refresh_from_db()

    assert ticket.status == Status.CLOSED


def test_a_message_moves_the_last_activity_stamp(ticket: Ticket, agent: Any) -> None:
    was = ticket.last_message_at

    message = post_message(ticket, agent, "Hello.")
    ticket.refresh_from_db()

    assert ticket.last_message_at == message.created_at
    assert was is None or ticket.last_message_at > was


def test_an_event_message_is_neither_a_response_nor_a_reopen(agent: Any, ticket: Ticket) -> None:
    set_status(ticket, str(Status.RESOLVED))

    post_message(ticket, agent, "resolved this", kind=str(MessageKind.EVENT))
    ticket.refresh_from_db()

    assert ticket.status == Status.RESOLVED
    assert ticket.first_response_at is None


# -- confidentiality -------------------------------------------------------


def test_a_note_is_forced_internal_whatever_the_caller_asked_for(
    agent: Any, ticket: Ticket
) -> None:
    message = post_message(
        ticket, agent, "Internal", kind=str(MessageKind.NOTE), visibility=str(Visibility.PUBLIC)
    )

    assert message.visibility == Visibility.INTERNAL


def test_the_database_refuses_a_public_note(agent: Any, ticket: Ticket) -> None:
    """The constraint is the guarantee; `post_message` is only the convenience."""
    with pytest.raises(IntegrityError):
        Message.objects.create(
            ticket=ticket, author=agent, kind=MessageKind.NOTE, visibility=Visibility.PUBLIC
        )


def test_clean_says_what_the_constraint_says(agent: Any, ticket: Ticket) -> None:
    message = Message(
        ticket=ticket, author=agent, kind=MessageKind.NOTE, visibility=Visibility.PUBLIC
    )

    with pytest.raises(ValidationError):
        message.clean()


def test_a_client_cannot_read_an_internal_note(
    client_user: Any, agent: Any, ticket: Ticket
) -> None:
    post_message(ticket, agent, "A note", kind=str(MessageKind.NOTE))
    post_message(ticket, agent, "A reply")

    readable = Message.objects.filter(ticket=ticket).readable_by(client_user)

    assert all(message.visibility == Visibility.PUBLIC for message in readable)
    assert readable.filter(kind=MessageKind.NOTE).count() == 0


def test_staff_read_everything_in_the_thread(agent: Any, ticket: Ticket) -> None:
    post_message(ticket, agent, "A note", kind=str(MessageKind.NOTE))

    assert Message.objects.filter(ticket=ticket).readable_by(agent).count() == 2


def test_a_client_sees_only_their_own_tickets(
    client_user: Any, other_client: Any, ticket: Ticket
) -> None:
    create_ticket(other_client, subject="Something else")

    assert list(Ticket.objects.visible_to(client_user)) == [ticket]


def test_staff_see_every_ticket(agent: Any, client_user: Any, other_client: Any) -> None:
    create_ticket(client_user, subject="One")
    create_ticket(other_client, subject="Two")

    assert Ticket.objects.visible_to(agent).count() == 2


def test_an_observer_sees_a_ticket_they_were_added_to(other_client: Any, ticket: Ticket) -> None:
    join(ticket, other_client, role=str(Role.OBSERVER))

    assert list(Ticket.objects.visible_to(other_client)) == [ticket]


# -- read watermarks -------------------------------------------------------


def test_your_own_messages_are_never_unread(client_user: Any, ticket: Ticket) -> None:
    """A client who sent three messages and got no reply has a badge of zero."""
    post_message(ticket, client_user, "And another thing.")

    assert unread_count(ticket, client_user) == 0


def test_a_reply_is_unread_until_it_is_read(client_user: Any, agent: Any, ticket: Ticket) -> None:
    post_message(ticket, agent, "Here is the answer.")
    assert unread_count(ticket, client_user) == 1

    assert mark_read(ticket, client_user) is True
    assert unread_count(ticket, client_user) == 0


def test_an_internal_note_is_not_unread_for_the_client(
    client_user: Any, agent: Any, ticket: Ticket
) -> None:
    post_message(ticket, agent, "A note", kind=str(MessageKind.NOTE))

    assert unread_count(ticket, client_user) == 0
    assert unread_count(ticket, agent) == 1  # the client's own opening message


def test_reading_twice_changes_nothing_the_second_time(
    client_user: Any, agent: Any, ticket: Ticket
) -> None:
    post_message(ticket, agent, "Answer.")
    mark_read(ticket, client_user)

    assert mark_read(ticket, client_user) is False


def test_a_watermark_never_moves_backwards(client_user: Any, agent: Any, ticket: Ticket) -> None:
    """Two tabs reporting what they read must not let the older one un-read."""
    post_message(ticket, agent, "Answer.")
    mark_read(ticket, client_user)
    watermark = Participant.objects.get(ticket=ticket, user=client_user).last_read_at
    assert watermark is not None

    assert mark_read(ticket, client_user, at=watermark - timezone.timedelta(hours=1)) is False
    assert Participant.objects.get(ticket=ticket, user=client_user).last_read_at == watermark


def test_marking_unread_puts_the_whole_thread_back(
    client_user: Any, agent: Any, ticket: Ticket
) -> None:
    post_message(ticket, agent, "Answer.")
    mark_read(ticket, client_user)

    assert mark_unread(ticket, client_user) is True
    assert unread_count(ticket, client_user) == 1
    assert mark_unread(ticket, client_user) is False


def test_the_annotated_unread_count_agrees_with_the_computed_one(
    client_user: Any, agent: Any, ticket: Ticket
) -> None:
    """The queue annotation and the per-thread helper are two ways to one number,
    and a queue showing a different count from the thread it links to is the bug
    worth a test of its own."""
    post_message(ticket, agent, "One.")
    post_message(ticket, agent, "Two.")

    annotated = Ticket.objects.visible_to(client_user).with_unread(client_user).get()

    assert annotated.unread == unread_count(ticket, client_user) == 2


def test_the_annotated_count_is_right_for_somebody_who_has_read_nothing(
    other_client: Any, agent: Any, ticket: Ticket
) -> None:
    """The null-watermark case: SQL comparisons against NULL are NULL, so this is
    the one that would silently come back zero."""
    join(ticket, other_client, role=str(Role.OBSERVER))
    post_message(ticket, agent, "Hello.")

    annotated = Ticket.objects.visible_to(other_client).with_unread(other_client).get()

    assert annotated.unread == 2


def test_total_unread_adds_up_across_threads(client_user: Any, agent: Any, ticket: Ticket) -> None:
    second = create_ticket(client_user, subject="Another")
    post_message(ticket, agent, "One.")
    post_message(second, agent, "Two.")

    assert total_unread(client_user) == 2


# -- editing and deleting --------------------------------------------------


def test_an_edit_records_that_it_was_edited(client_user: Any, ticket: Ticket) -> None:
    message = post_message(ticket, client_user, "Frist message")

    edit_message(message, "First message")

    assert message.body == "First message"
    assert message.edited_at is not None


def test_a_deletion_is_a_tombstone_rather_than_a_delete(client_user: Any, ticket: Ticket) -> None:
    message = post_message(ticket, client_user, "Oops, my card number is 4111")

    delete_message(message)

    assert Message.objects.filter(pk=message.pk).exists()
    assert message.is_deleted is True
    assert Message.objects.filter(ticket=ticket).alive().count() == 1


def test_only_the_author_may_edit(client_user: Any, agent: Any, ticket: Ticket) -> None:
    message = post_message(ticket, client_user, "Mine")

    assert message.editable_by(client_user) is True
    assert message.editable_by(agent) is False


def test_staff_may_delete_what_a_client_posted(
    client_user: Any, agent: Any, ticket: Ticket
) -> None:
    message = post_message(ticket, client_user, "A card number")

    assert message.deletable_by(agent) is True
    assert message.deletable_by(client_user) is True


def test_an_event_is_neither_editable_nor_deletable(agent: Any, ticket: Ticket) -> None:
    event = post_message(ticket, agent, "closed this", kind=str(MessageKind.EVENT))

    assert event.editable_by(agent) is False
    assert event.deletable_by(agent) is False


def test_a_deleted_message_cannot_be_deleted_again(client_user: Any, ticket: Ticket) -> None:
    message = delete_message(post_message(ticket, client_user, "Gone"))

    assert message.deletable_by(client_user) is False
    assert message.editable_by(client_user) is False


# -- status, assignment, priority, rating ----------------------------------


def test_resolving_stamps_the_time_and_closing_stamps_both(ticket: Ticket) -> None:
    set_status(ticket, str(Status.RESOLVED))
    assert ticket.resolved_at is not None
    assert ticket.closed_at is None

    set_status(ticket, str(Status.CLOSED))
    assert ticket.closed_at is not None
    assert ticket.resolved_at is not None


def test_reviving_a_settled_ticket_clears_both_stamps(ticket: Ticket) -> None:
    set_status(ticket, str(Status.CLOSED))

    set_status(ticket, str(Status.OPEN))

    assert ticket.resolved_at is None
    assert ticket.closed_at is None


def test_setting_the_status_it_already_has_changes_nothing(ticket: Ticket) -> None:
    assert set_status(ticket, str(Status.OPEN)) is False


def test_assigning_puts_the_agent_in_the_thread(ticket: Ticket, agent: Any) -> None:
    assert assign(ticket, agent) is True
    assert ticket.participants.filter(user=agent, role=Role.AGENT).exists()
    assert assign(ticket, agent) is False


def test_unassigning_leaves_the_agent_in_the_thread(ticket: Ticket, agent: Any) -> None:
    """They answered it; taking the ticket away does not unsay that."""
    assign(ticket, agent)

    assert assign(ticket, None) is True
    assert ticket.assignee is None
    assert ticket.participants.filter(user=agent).exists()


def test_priority_reports_whether_it_moved(ticket: Ticket) -> None:
    assert set_priority(ticket, str(Priority.URGENT)) is True
    assert set_priority(ticket, str(Priority.URGENT)) is False


def test_a_rating_can_be_changed(ticket: Ticket) -> None:
    set_status(ticket, str(Status.CLOSED))

    rate(ticket, 3, "Slow.")
    rate(ticket, 5, "Actually, sorted.")

    assert ticket.rating == 5
    assert ticket.rating_comment == "Actually, sorted."
    assert ticket.rated_at is not None


def test_the_database_refuses_a_rating_outside_one_to_five(ticket: Ticket) -> None:
    with pytest.raises(IntegrityError):
        Ticket.objects.filter(pk=ticket.pk).update(rating=9)


def test_clean_refuses_a_rating_while_the_ticket_is_open(ticket: Ticket) -> None:
    ticket.rating = 5

    with pytest.raises(ValidationError):
        ticket.clean()


def test_clean_refuses_an_assignee_who_is_not_staff(ticket: Ticket, other_client: Any) -> None:
    ticket.assignee = other_client

    with pytest.raises(ValidationError):
        ticket.clean()


# -- joining ---------------------------------------------------------------


def test_joining_is_idempotent(ticket: Ticket, agent: Any) -> None:
    first = join(ticket, agent)
    second = join(ticket, agent)

    assert first.pk == second.pk


def test_one_participation_per_account_is_a_constraint(ticket: Ticket, agent: Any) -> None:
    join(ticket, agent)

    with pytest.raises(IntegrityError):
        Participant.objects.create(ticket=ticket, user=agent, role=Role.OBSERVER)


# -- searching -------------------------------------------------------------


def test_search_matches_a_reference_a_subject_and_a_body(agent: Any, ticket: Ticket) -> None:
    assert Ticket.objects.search(ticket.reference).count() == 1
    assert Ticket.objects.search("charged twice").count() == 1
    assert Ticket.objects.search("two charges").count() == 1
    assert Ticket.objects.search("nothing like this").count() == 0


def test_a_client_cannot_find_a_ticket_by_a_word_only_in_an_internal_note(
    client_user: Any, agent: Any, ticket: Ticket
) -> None:
    """A term that matched something they cannot read would reveal that it exists."""
    post_message(ticket, agent, "Probably fraudulent", kind=str(MessageKind.NOTE))

    hers = Ticket.objects.visible_to(client_user).for_search_by(client_user, "fraudulent")

    assert hers.count() == 0
    assert Ticket.objects.visible_to(agent).for_search_by(agent, "fraudulent").count() == 1


def test_a_client_cannot_find_a_ticket_by_a_word_in_a_deleted_message(
    client_user: Any, ticket: Ticket
) -> None:
    delete_message(post_message(ticket, client_user, "retracted secret"))

    assert Ticket.objects.for_search_by(client_user, "retracted").count() == 0


def test_an_empty_search_narrows_nothing(client_user: Any, ticket: Ticket) -> None:
    assert Ticket.objects.for_search_by(client_user, "   ").count() == 1


# -- pruning ---------------------------------------------------------------


def test_pruning_deletes_closed_tickets_older_than_the_cutoff(ticket: Ticket) -> None:
    set_status(ticket, str(Status.CLOSED))
    Ticket.objects.filter(pk=ticket.pk).update(
        closed_at=timezone.now() - timezone.timedelta(days=100)
    )

    assert prune(timezone.now() - timezone.timedelta(days=30)) > 0
    assert not Ticket.objects.filter(pk=ticket.pk).exists()


def test_pruning_never_deletes_an_open_ticket(client_user: Any, ticket: Ticket) -> None:
    """However old it is, it is somebody's unanswered question."""
    Ticket.objects.filter(pk=ticket.pk).update(
        created_at=timezone.now() - timezone.timedelta(days=1000)
    )

    prune(timezone.now())

    assert Ticket.objects.filter(pk=ticket.pk).exists()


def test_pruning_never_deletes_a_resolved_but_unclosed_ticket(ticket: Ticket) -> None:
    set_status(ticket, str(Status.RESOLVED))
    Ticket.objects.filter(pk=ticket.pk).update(
        resolved_at=timezone.now() - timezone.timedelta(days=1000)
    )

    prune(timezone.now())

    assert Ticket.objects.filter(pk=ticket.pk).exists()


def test_pruning_uploads_leaves_a_claimed_one_alone(client_user: Any, ticket: Ticket) -> None:
    from apps.support.models import Upload

    stale = Upload.objects.create(owner=client_user, name="old.png", url="/old.png")
    claimed = Upload.objects.create(owner=client_user, name="used.png", url="/used.png")
    post_message(ticket, client_user, "Here", uploads=[claimed])
    Upload.objects.update(created_at=timezone.now() - timezone.timedelta(days=100))

    assert prune_uploads(timezone.now() - timezone.timedelta(days=7)) == 1
    assert not Upload.objects.filter(pk=stale.pk).exists()
    assert Upload.objects.filter(pk=claimed.pk).exists()


# -- odds and ends ---------------------------------------------------------


def test_a_ticket_says_its_reference_and_subject(ticket: Ticket) -> None:
    assert ticket.reference in str(ticket)
    assert "I was charged twice" in str(ticket)


def test_a_chat_with_no_subject_says_what_it_is(chat: Ticket) -> None:
    assert "Live chat" in str(chat)


def test_a_message_with_an_attachment_copies_what_the_upload_said(
    client_user: Any, ticket: Ticket
) -> None:
    from apps.support.models import Upload

    upload = Upload.objects.create(
        owner=client_user, name="shot.png", url="/media/shot.png", content_type="image/png", size=12
    )

    message = post_message(ticket, client_user, "See this", uploads=[upload])

    attachment = message.attachments.get()
    assert (attachment.name, attachment.url, attachment.size) == ("shot.png", "/media/shot.png", 12)
    assert upload.is_claimed is True
