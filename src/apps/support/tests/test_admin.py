"""The desk's admin: what the columns say, and what the screens will not let you do.

The columns are worth testing because a queue is read by scanning them -- an
SLA badge that says "on time" about a breached thread is worse than no badge --
and because the bulk actions save each row rather than updating in place, which
is the only reason a client watching a thread hears that it was closed.
"""

from datetime import timedelta
from typing import Any

import pytest
from django.contrib.admin.sites import AdminSite
from django.test import RequestFactory
from django.utils import timezone

from apps.support.admin import (
    CategoryAdmin,
    MessageAdmin,
    TagAdmin,
    TicketAdmin,
    desk_numbers,
)
from apps.support.models import (
    Category,
    Message,
    Priority,
    Status,
    Tag,
    Ticket,
    assign,
    create_ticket,
    delete_message,
    post_message,
    set_status,
)

pytestmark = pytest.mark.django_db


def _tickets() -> TicketAdmin:
    return TicketAdmin(Ticket, AdminSite())


def _row(ticket: Ticket) -> Ticket:
    """Refetch through the admin's queryset, which is where the counts come from."""
    request = RequestFactory().get("/")
    return _tickets().get_queryset(request).get(pk=ticket.pk)


def _request() -> Any:
    """A request the admin's ``message_user`` can write onto."""
    request = RequestFactory().post("/")
    request.session = "session"
    request._messages = FakeMessages()
    return request


class FakeMessages:
    """Collects what the admin told the operator, so a test can read it back."""

    def __init__(self) -> None:
        self.said: list[str] = []

    def add(self, level: int, message: str, extra_tags: str = "") -> None:
        self.said.append(message)


# -- the queue's columns ----------------------------------------------------


def test_a_chat_with_no_subject_says_what_it_is_instead_of_nothing(chat: Ticket) -> None:
    assert _tickets().subject_or_kind(chat) == "(Live chat)"


def test_a_ticket_shows_its_subject(ticket: Ticket) -> None:
    assert _tickets().subject_or_kind(ticket) == "I was charged twice"


def test_the_status_and_priority_columns_are_coloured(ticket: Ticket) -> None:
    assert "#dc2626" in _tickets().status_badge(ticket)
    assert "Open" in _tickets().status_badge(ticket)
    assert "#2563eb" in _tickets().priority_badge(ticket)


def test_a_status_with_no_colour_still_renders(ticket: Ticket) -> None:
    ticket.status = "whatever"

    assert "#6b7280" in _tickets().status_badge(ticket)


def test_a_priority_with_no_colour_still_renders(ticket: Ticket) -> None:
    ticket.priority = "whatever"

    assert "#6b7280" in _tickets().priority_badge(ticket)


def test_the_waiting_column_counts_from_the_last_message(ticket: Ticket) -> None:
    assert _tickets().waiting(ticket) == "0 min"

    Ticket.objects.filter(pk=ticket.pk).update(last_message_at=timezone.now() - timedelta(days=2))

    assert _tickets().waiting(Ticket.objects.get(pk=ticket.pk)) == "2 days"


def test_the_waiting_column_rounds_to_hours_in_between(ticket: Ticket) -> None:
    Ticket.objects.filter(pk=ticket.pk).update(last_message_at=timezone.now() - timedelta(hours=1))

    assert _tickets().waiting(Ticket.objects.get(pk=ticket.pk)) == "1 hour"


def test_a_settled_thread_is_not_waiting_for_anything(ticket: Ticket) -> None:
    set_status(ticket, str(Status.CLOSED))

    assert _tickets().waiting(ticket) == "--"


def test_a_thread_with_no_promise_has_no_sla_column(chat: Ticket) -> None:
    assert _tickets().sla_state(chat) == "--"


def test_a_thread_inside_its_promise_reads_as_a_reply_due(ticket: Ticket) -> None:
    badge = _tickets().sla_state(ticket)

    assert "Reply due in" in badge
    assert "#d97706" in badge


def test_a_thread_that_has_been_answered_reads_as_on_time(ticket: Ticket, agent: Any) -> None:
    post_message(ticket, agent, "Looking into it.")

    badge = _tickets().sla_state(Ticket.objects.get(pk=ticket.pk))

    assert "On time" in badge


def test_a_missed_promise_says_which_one_was_missed(ticket: Ticket) -> None:
    Ticket.objects.filter(pk=ticket.pk).update(
        first_response_due_at=timezone.now() - timedelta(hours=1),
        resolution_due_at=timezone.now() - timedelta(hours=1),
    )

    badge = _tickets().sla_state(Ticket.objects.get(pk=ticket.pk))

    assert "Missed first reply and resolution" in badge
    assert "#dc2626" in badge


def test_the_message_count_comes_from_the_list_query(ticket: Ticket, client_user: Any) -> None:
    assert _tickets().messages(_row(ticket)) == 1

    post_message(ticket, client_user, "Still waiting.")

    assert _tickets().messages(_row(ticket)) == 2


def test_a_retracted_message_stops_being_counted(ticket: Ticket, client_user: Any) -> None:
    extra = post_message(ticket, client_user, "Ignore that.")

    delete_message(extra)

    assert _tickets().messages(_row(ticket)) == 1


def test_a_ticket_the_admin_has_not_annotated_counts_as_none(ticket: Ticket) -> None:
    """The column is read from a row that was fetched some other way often enough."""
    assert _tickets().messages(ticket) == 0


# -- the bulk actions -------------------------------------------------------


def test_marking_selected_resolved_saves_each_row(ticket: Ticket) -> None:
    """A ``update`` would skip ``post_save`` and nobody watching would hear."""
    request = _request()

    _tickets().mark_resolved(request, Ticket.objects.filter(pk=ticket.pk))

    assert Ticket.objects.get(pk=ticket.pk).status == Status.RESOLVED
    assert request._messages.said == ["1 ticket updated."]


def test_marking_selected_closed_reports_how_many_actually_moved(
    ticket: Ticket, client_user: Any
) -> None:
    already = create_ticket(client_user, subject="Old one")
    set_status(already, str(Status.CLOSED))
    request = _request()

    _tickets().mark_closed(request, Ticket.objects.all())

    assert request._messages.said == ["1 ticket updated."]


def test_returning_selected_to_the_unassigned_queue(ticket: Ticket, agent: Any) -> None:
    assign(ticket, agent)
    request = _request()

    _tickets().unassign(request, Ticket.objects.filter(pk=ticket.pk))

    assert Ticket.objects.get(pk=ticket.pk).assignee is None
    assert request._messages.said == ["1 ticket unassigned."]


def test_unassigning_something_already_unassigned_changes_nothing(
    ticket: Ticket,
) -> None:
    request = _request()

    _tickets().unassign(request, Ticket.objects.filter(pk=ticket.pk))

    assert request._messages.said == ["0 tickets unassigned."]


# -- the other screens ------------------------------------------------------


def test_a_categorys_promises_are_shown_in_the_unit_a_person_says_them_in(
    category: Category, slow_category: Category
) -> None:
    admin = CategoryAdmin(Category, AdminSite())

    assert admin.first_response_promise(category) == "1 hour"
    assert admin.resolution_promise(category) == "1 day"
    assert admin.first_response_promise(slow_category) == "no promise"


def test_an_odd_promise_falls_back_to_minutes(slow_category: Category) -> None:
    slow_category.first_response_minutes = 90

    assert CategoryAdmin(Category, AdminSite()).first_response_promise(slow_category) == ("90 min")


def test_a_category_counts_the_tickets_filed_under_it(category: Category, ticket: Ticket) -> None:
    admin = CategoryAdmin(Category, AdminSite())
    row = admin.get_queryset(RequestFactory().get("/")).get(pk=category.pk)

    assert admin.tickets(row) == 1
    assert admin.tickets(category) == 0


def test_a_tag_shows_its_colour_as_a_swatch(tag: Tag) -> None:
    admin = TagAdmin(Tag, AdminSite())

    assert "#dc2626" in admin.swatch(tag)


def test_a_tag_with_no_colour_shows_no_swatch(db: None) -> None:
    plain = Tag.objects.create(name="Plain", colour="")

    assert TagAdmin(Tag, AdminSite()).swatch(plain) == "--"


def test_a_tag_counts_what_it_is_on(tag: Tag, ticket: Ticket) -> None:
    ticket.tags.add(tag)
    admin = TagAdmin(Tag, AdminSite())
    row = admin.get_queryset(RequestFactory().get("/")).get(pk=tag.pk)

    assert admin.tickets(row) == 1


def test_a_message_is_previewed_by_its_first_line(ticket: Ticket, client_user: Any) -> None:
    admin = MessageAdmin(Message, AdminSite())
    message = post_message(ticket, client_user, "First line.\nSecond line.")

    assert admin.preview(message) == "First line."


def test_a_long_preview_is_cut(ticket: Ticket, client_user: Any) -> None:
    admin = MessageAdmin(Message, AdminSite())
    message = post_message(ticket, client_user, "x" * 200)

    assert admin.preview(message).endswith("...")
    assert len(admin.preview(message)) == 83


def test_a_message_that_is_only_an_attachment_previews_as_nothing(
    ticket: Ticket, client_user: Any
) -> None:
    """A body can be empty when the message is carrying a file instead."""
    admin = MessageAdmin(Message, AdminSite())
    message = Message.objects.create(ticket=ticket, author=client_user, body="")

    assert admin.preview(message) == ""


def test_a_retracted_message_says_so_rather_than_showing_its_body(
    ticket: Ticket, client_user: Any
) -> None:
    admin = MessageAdmin(Message, AdminSite())
    message = delete_message(post_message(ticket, client_user, "Oops, my card number."))

    assert admin.preview(message) == "(deleted)"


def test_a_message_cannot_be_added_from_the_admin(db: None) -> None:
    """Anything sent from here would have no thread to be delivered on."""
    assert MessageAdmin(Message, AdminSite()).has_add_permission(RequestFactory().get("/")) is (
        False
    )


# -- the dashboard card -----------------------------------------------------


def test_the_dashboard_counts_what_the_desk_is_carrying(
    ticket: Ticket, client_user: Any, agent: Any
) -> None:
    settled = create_ticket(client_user, subject="Sorted already")
    set_status(settled, str(Status.CLOSED))
    answered = create_ticket(client_user, subject="Being handled")
    post_message(answered, agent, "On it.")

    numbers = desk_numbers()

    assert numbers["live"] == 2
    assert numbers["unassigned"] == 2
    assert numbers["awaiting"] == 1
    assert numbers["breached"] == 0


def test_the_dashboard_counts_a_breached_promise(ticket: Ticket) -> None:
    Ticket.objects.filter(pk=ticket.pk).update(
        first_response_due_at=timezone.now() - timedelta(hours=1)
    )

    assert desk_numbers()["breached"] == 1


def test_every_priority_and_status_has_a_deliberate_colour() -> None:
    """The grey fallback is for a value the app does not know; none of these are."""
    from apps.support.admin import PRIORITY_COLOURS, STATUS_COLOURS

    assert set(PRIORITY_COLOURS) == set(Priority.values)
    assert set(STATUS_COLOURS) == set(Status.values)
