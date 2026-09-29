"""The guards an audit found missing, one test for each.

Each of these is a rule the rest of the suite assumed held and nothing checked:
a badge that counts what you cannot clear, a write deciding on a stale copy of
its thread, a staff override reaching into a room, a column limit the database
enforces with a 500. They are kept together so the next audit can see at a
glance which were already found.
"""

from io import StringIO
from typing import Any

import pytest
from django.contrib.admin.sites import AdminSite
from django.core.management import CommandError, call_command
from django.test import RequestFactory

from apps.support import broadcast
from apps.support.admin import MessageInline, TicketAdmin
from apps.support.models import (
    Kind,
    Message,
    MessageKind,
    Status,
    Ticket,
    create_ticket,
    direct_key_for,
    post_message,
    set_status,
    total_unread,
)
from apps.support.services import (
    InvalidRequest,
    NotPermitted,
    SupportService,
    support_service,
)

pytestmark = pytest.mark.django_db


class Frames:
    """Every frame the broker is handed, by channel."""

    def __init__(self) -> None:
        self.sent: list[tuple[str, dict[str, Any]]] = []

    def publish(self, channel: str, payload: dict[str, Any]) -> None:
        self.sent.append((channel, payload))

    def on(self, channel: str) -> list[dict[str, Any]]:
        return [frame for name, frame in self.sent if name == channel]


@pytest.fixture
def frames(monkeypatch: pytest.MonkeyPatch) -> Frames:
    recorder = Frames()
    monkeypatch.setattr(broadcast.get_broker(), "publish", recorder.publish)
    return recorder


def _stale(monkeypatch: pytest.MonkeyPatch, ticket: Ticket) -> None:
    """Make the service's first read return this copy, as a racing request would."""
    monkeypatch.setattr(SupportService, "_ticket", lambda self, user, ticket_id: ticket)


# -- the badge ---------------------------------------------------------------


def test_an_unjoined_channel_adds_nothing_to_the_badge(client_user: Any, other_client: Any) -> None:
    support_service.create_channel(client_user, "General", body="hello all")

    assert support_service.unread(other_client) == {"messages": 0, "tickets": 0}
    assert total_unread(other_client) == 0


# -- writes that decide on the thread's status --------------------------------


def test_a_reply_racing_a_close_is_refused_rather_than_reopening(
    monkeypatch: pytest.MonkeyPatch, client_user: Any, ticket: Ticket
) -> None:
    stale = Ticket.objects.get(pk=ticket.pk)
    set_status(ticket, str(Status.CLOSED))
    _stale(monkeypatch, stale)

    with pytest.raises(NotPermitted):
        support_service.send(client_user, ticket.pk, "one more thing")

    ticket.refresh_from_db()
    assert (ticket.status, ticket.closed_at is not None) == (Status.CLOSED, True)


def test_a_rating_racing_a_reopen_is_refused(
    monkeypatch: pytest.MonkeyPatch, client_user: Any, ticket: Ticket
) -> None:
    set_status(ticket, str(Status.RESOLVED))
    stale = Ticket.objects.get(pk=ticket.pk)
    set_status(ticket, str(Status.OPEN))
    _stale(monkeypatch, stale)

    with pytest.raises(NotPermitted):
        support_service.rate(client_user, ticket.pk, 5)

    ticket.refresh_from_db()
    assert (ticket.status, ticket.rating) == (Status.OPEN, None)


def test_a_status_change_moves_the_current_row_not_the_stale_copy(
    monkeypatch: pytest.MonkeyPatch, agent: Any, ticket: Ticket
) -> None:
    stale = Ticket.objects.get(pk=ticket.pk)
    set_status(ticket, str(Status.CLOSED))
    _stale(monkeypatch, stale)

    assert support_service.status(agent, ticket.pk, str(Status.CLOSED))["changed"] is False


# -- rooms are not the desk ------------------------------------------------------


def test_staff_cannot_delete_somebody_elses_message_in_a_channel(
    client_user: Any, agent: Any
) -> None:
    channel = support_service.create_channel(client_user, "General", body="hello all")
    message = Message.objects.get(ticket_id=channel["id"])

    with pytest.raises(NotPermitted):
        support_service.delete(agent, message.pk)


def test_staff_can_still_retract_a_clients_message_on_the_desk(
    client_user: Any, agent: Any, ticket: Ticket
) -> None:
    message = post_message(ticket, client_user, "my card is 4111...")

    assert support_service.delete(agent, message.pk)["deleted"] is True


def test_a_staff_post_in_a_room_moves_no_desk_status(client_user: Any, agent: Any) -> None:
    channel = support_service.create_channel(client_user, "General")
    support_service.join_room(agent, channel["id"])

    support_service.send(agent, channel["id"], "hi")

    room = Ticket.objects.get(pk=channel["id"])
    assert (room.status, room.first_response_at) == (Status.OPEN, None)


# -- arguments --------------------------------------------------------------------


def test_a_malformed_assignee_filter_is_an_invalid_request(agent: Any) -> None:
    with pytest.raises(InvalidRequest):
        support_service.tickets(agent, assignee="nope")


@pytest.mark.parametrize(
    "call",
    [
        lambda user: support_service.create_channel(user, "x" * 201),
        lambda user: support_service.create_channel(user, "ok", slug="y" * 141),
        lambda user: support_service.open(user, subject="x" * 201, body="help"),
    ],
)
def test_a_value_longer_than_its_column_is_refused(client_user: Any, call: Any) -> None:
    with pytest.raises(InvalidRequest):
        call(client_user)


def test_a_group_name_longer_than_its_column_is_refused(
    client_user: Any, other_client: Any
) -> None:
    with pytest.raises(InvalidRequest):
        support_service.create_group(client_user, "x" * 201, [other_client.pk])


def test_opening_an_existing_private_chat_joins_the_caller(
    client_user: Any, other_client: Any
) -> None:
    """The half-made chat another request is still creating is the caller's too."""
    ticket = create_ticket(
        other_client,
        kind=str(Kind.DIRECT),
        direct_key=direct_key_for(client_user.pk, other_client.pk),
    )

    assert support_service.direct(client_user, other_client.pk)["id"] == str(ticket.pk)


def test_upload_days_below_one_is_refused(settings: Any) -> None:
    settings.SUPPORT_RETENTION_DAYS = 30
    for days in ("0", "-1"):
        with pytest.raises(CommandError):
            call_command("support_prune", "--upload-days", days, stdout=StringIO())


# -- what reaches the client --------------------------------------------------------


def test_an_internal_note_leaves_the_clients_view_of_the_thread_alone(
    frames: Frames,
    client_user: Any,
    agent: Any,
    ticket: Ticket,
    django_capture_on_commit_callbacks: Any,
) -> None:
    before = Ticket.objects.get(pk=ticket.pk)
    with django_capture_on_commit_callbacks(execute=True):
        support_service.send(agent, ticket.pk, "between us", internal=True)

    after = Ticket.objects.get(pk=ticket.pk)
    assert (after.last_message_at, after.updated_at) == (before.last_message_at, before.updated_at)
    assert frames.on(broadcast.user_channel(client_user.pk)) == []


def test_an_onlooking_agents_read_is_not_announced_to_the_client(
    frames: Frames,
    client_user: Any,
    agent: Any,
    ticket: Ticket,
    django_capture_on_commit_callbacks: Any,
) -> None:
    with django_capture_on_commit_callbacks(execute=True):
        support_service.read(agent, ticket.pk)

    reads = [f for f in frames.on(broadcast.ticket_channel(ticket.pk)) if f["type"] == "read"]
    assert reads == []


def test_sign_in_subscriptions_skip_unjoined_channels(client_user: Any, other_client: Any) -> None:
    from apps.support.sockets import _live_ticket_ids

    support_service.create_channel(client_user, "General")

    assert _live_ticket_ids(other_client) == []


# -- notifications ---------------------------------------------------------------------


def test_a_long_subject_is_cut_to_fit_the_notification(
    client_user: Any, agent: Any, django_capture_on_commit_callbacks: Any
) -> None:
    from apps.notifications.models import Notification

    opened = support_service.open(client_user, subject="x" * 200, body="help")
    support_service.claim(agent, opened["id"])
    with django_capture_on_commit_callbacks(execute=True):
        support_service.send(agent, opened["id"], "on it")

    subject = Notification.objects.get(recipient=client_user).subject
    assert len(subject) <= Notification._meta.get_field("subject").max_length


def test_a_failing_notification_does_not_undo_the_message(
    monkeypatch: pytest.MonkeyPatch,
    client_user: Any,
    agent: Any,
    ticket: Ticket,
    django_capture_on_commit_callbacks: Any,
) -> None:
    def broken(*args: Any, **kwargs: Any) -> None:
        raise RuntimeError("the bell is broken")

    monkeypatch.setattr("apps.notifications.events.notify_users", broken)
    support_service.claim(agent, ticket.pk)
    with django_capture_on_commit_callbacks(execute=True):
        sent = support_service.send(agent, ticket.pk, "on it")

    assert Message.objects.filter(pk=sent["id"]).exists()


# -- the admin ---------------------------------------------------------------------------


def _admin() -> TicketAdmin:
    return TicketAdmin(Ticket, AdminSite())


def _request(user: Any = None) -> Any:
    request = RequestFactory().post("/")
    request.user = user
    request.session = "session"
    request._messages = type("M", (), {"add": lambda *a, **k: None})()
    return request


def test_status_and_kind_are_read_only_once_saved(ticket: Ticket) -> None:
    request = _request()
    assert {"status", "kind"} <= set(_admin().get_readonly_fields(request, ticket))
    assert "status" not in _admin().get_readonly_fields(request, None)


def test_a_ticket_added_closed_gets_its_close_date(client_user: Any, agent: Any) -> None:
    obj = Ticket(client=client_user, subject="Filed by hand", status=Status.CLOSED)

    _admin().save_model(_request(agent), obj, None, False)

    obj.refresh_from_db()
    assert (obj.status, obj.closed_at is not None) == (Status.CLOSED, True)


def test_bulk_close_skips_rooms_and_records_the_move(
    client_user: Any, agent: Any, ticket: Ticket
) -> None:
    channel = support_service.create_channel(client_user, "General")

    _admin().mark_closed(_request(agent), Ticket.objects.all())

    assert Ticket.objects.get(pk=channel["id"]).status == Status.OPEN
    assert Message.objects.filter(ticket=ticket, kind=MessageKind.EVENT).exists()


def test_private_rooms_are_not_in_the_admin(
    client_user: Any, other_client: Any, agent: Any, ticket: Ticket
) -> None:
    dm = support_service.direct(client_user, other_client.pk)
    group = support_service.create_group(client_user, "Us", [other_client.pk], body="psst")
    request = RequestFactory().get("/")
    request.user = agent

    listed = {str(pk) for pk in _admin().get_queryset(request).values_list("pk", flat=True)}
    inline = MessageInline(Ticket, AdminSite()).get_queryset(request)

    assert str(ticket.pk) in listed
    assert listed.isdisjoint({dm["id"], group["id"]})
    assert not inline.filter(ticket_id=group["id"]).exists()


def test_the_queue_filters_by_a_well_formed_assignee(agent: Any, ticket: Any) -> None:
    support_service.claim(agent, ticket.pk)

    assert [row["id"] for row in support_service.tickets(agent, assignee=str(agent.pk))] == [
        str(ticket.pk)
    ]


def test_a_channel_address_taken_mid_request_is_refused(
    client_user: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The existence check passed; the insert lost the race to somebody else's."""
    from django.db import IntegrityError

    from apps.support import services

    def taken(*args: Any, **kwargs: Any) -> Any:
        raise IntegrityError("duplicate slug")

    monkeypatch.setattr(services, "create_ticket", taken)

    with pytest.raises(services.InvalidRequest, match="already a channel"):
        support_service.create_channel(client_user, "General")


def test_a_private_chat_opened_by_both_at_once_ends_in_one_chat(
    client_user: Any, other_client: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The other person's insert wins; this caller is put in their chat."""
    from django.db import IntegrityError

    from apps.support import services

    real = services.create_ticket

    def lost_the_race(user: Any, **kwargs: Any) -> Any:
        real(other_client, **kwargs)
        raise IntegrityError("duplicate direct_key")

    monkeypatch.setattr(services, "create_ticket", lost_the_race)

    chat = support_service.direct(client_user, other_client.pk)

    monkeypatch.setattr(services, "create_ticket", real)
    assert support_service.direct(other_client, client_user.pk)["id"] == chat["id"]
