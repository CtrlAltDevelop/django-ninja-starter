"""What the desk puts in the admin, and who is shown it.

The app owns its own sidebar group and dashboard section -- see
:mod:`infrastructure.common.adminui` -- so the thing worth testing is that the
numbers are the desk's real ones and that an account which may not read tickets
is offered neither.
"""

from typing import Any

import pytest
from django.contrib.auth.models import Permission
from django.test import RequestFactory

from apps.support.adminui import dashboard, navigation
from apps.support.models import Kind, Status, Ticket, create_ticket, post_message, set_status

pytestmark = pytest.mark.django_db


def _request(user: Any) -> Any:
    request = RequestFactory().get("/admin/")
    request.user = user
    return request


def _asked(client: Any) -> Any:
    """A thread with the client's question in it and nobody's answer yet."""
    ticket = create_ticket(client, subject="my printer is on fire")
    post_message(ticket, client, "my printer is on fire")
    return ticket


def _cards(user: Any) -> dict[str, Any]:
    section = dashboard(_request(user))
    assert section is not None
    return {card["label"]: card["value"] for card in section["cards"]}


def test_a_thread_nobody_has_answered_is_the_first_number(
    admin_user: Any, client_user: Any
) -> None:
    _asked(client_user)

    assert _cards(admin_user)["Waiting for an answer"] == 1


def test_answering_it_takes_it_out_of_that_number(
    admin_user: Any, agent: Any, client_user: Any
) -> None:
    ticket = _asked(client_user)
    post_message(ticket, agent, "have you tried water")

    cards = _cards(admin_user)

    assert cards["Waiting for an answer"] == 0
    assert cards["Open conversations"] == 1


def test_a_settled_thread_is_not_an_open_conversation(admin_user: Any, client_user: Any) -> None:
    ticket = _asked(client_user)
    set_status(ticket, str(Status.CLOSED))

    assert _cards(admin_user)["Open conversations"] == 0


def test_somebody_who_may_not_read_tickets_is_shown_no_section(agent: Any) -> None:
    """And no links either: half a dashboard beats cards that answer 403."""
    assert dashboard(_request(agent)) is None


def test_the_desk_lists_its_screens_for_somebody_who_may_open_them(agent: Any) -> None:
    agent.user_permissions.add(Permission.objects.get(codename="view_ticket"))
    titles = [item["title"] for item in navigation(_request(agent))["items"]]

    assert titles[0] == "Live chat"
    assert "Tickets" in titles


def test_every_link_it_offers_resolves(admin_user: Any) -> None:
    """A lazy reverse only fails when it is rendered, which is too late."""
    for item in navigation(_request(admin_user))["items"]:
        assert str(item["link"]).startswith("/")


def test_the_numbers_are_counted_over_desk_threads(admin_user: Any, client_user: Any) -> None:
    """A room is not the desk's work, and must not inflate its queue."""
    _asked(client_user)
    Ticket.objects.create(kind=Kind.GROUP, client=client_user, subject="a private group")

    assert _cards(admin_user)["Open conversations"] == 1
