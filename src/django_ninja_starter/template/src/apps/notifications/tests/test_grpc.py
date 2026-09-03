"""Reading notifications over gRPC.

`transactional_db` throughout: the server answers on its own connection and
cannot see rows still sitting in a test's open transaction.
"""

import json
from collections.abc import Callable
from typing import Any

import grpc
import pytest
from django.contrib.auth import get_user_model
from django.test import RequestFactory
from google.protobuf.empty_pb2 import Empty

from apps.notifications.grpc import notifications_pb2, notifications_pb2_grpc
from apps.notifications.models import Audience, Notification

Stub = notifications_pb2_grpc.NotificationControllerStub


@pytest.fixture
def alice(transactional_db: None) -> Any:
    return get_user_model().objects.create_user(username="alice", email="alice@example.test")


@pytest.fixture
def token(alice: Any) -> str:
    from infrastructure.auth.core.sessions import issue_credentials

    request = RequestFactory().post("/")
    access: str = issue_credentials(request, alice, method="password").access_token
    return access


@pytest.fixture
def mail(alice: Any) -> tuple[Notification, Notification]:
    announcement = Notification.objects.create(
        audience=Audience.GLOBAL, subject="Scheduled maintenance", body="Sunday, 02:00 UTC."
    )
    mine = Notification.objects.create(
        audience=Audience.USER,
        recipient=alice,
        subject="Your export is ready",
        data={"export": 1},
    )
    return announcement, mine


def test_the_list_needs_a_credential(transactional_db: None, grpc_call: Callable[..., Any]) -> None:
    with pytest.raises(grpc.aio.AioRpcError) as refusal:
        grpc_call(Stub, "List", notifications_pb2.ListRequest())

    assert refusal.value.code() is grpc.StatusCode.UNAUTHENTICATED


def test_the_list_mixes_what_is_addressed_to_everybody_with_what_is_mine(
    mail: tuple[Notification, Notification], token: str, grpc_call: Callable[..., Any]
) -> None:
    reply = grpc_call(Stub, "List", notifications_pb2.ListRequest(), token=token)

    subjects = {row.subject for row in reply.notifications}
    assert subjects == {mail[0].subject, mail[1].subject}


def test_free_form_data_survives_as_json(
    mail: tuple[Notification, Notification], token: str, grpc_call: Callable[..., Any]
) -> None:
    """Protobuf has no `Any`, so the payload travels as the JSON it already was."""
    reply = grpc_call(Stub, "List", notifications_pb2.ListRequest(), token=token)

    mine = next(row for row in reply.notifications if row.subject == "Your export is ready")
    assert json.loads(mine.data_json) == {"export": 1}


def test_marking_everything_read_leaves_nothing_outstanding(
    mail: tuple[Notification, Notification], token: str, grpc_call: Callable[..., Any]
) -> None:
    reply = grpc_call(Stub, "ReadAll", Empty(), token=token)

    assert reply.count == 2
    assert reply.unread == 0


def test_an_unknown_notification_is_not_found(token: str, grpc_call: Callable[..., Any]) -> None:
    with pytest.raises(grpc.aio.AioRpcError) as refusal:
        grpc_call(
            Stub,
            "Read",
            notifications_pb2.ReadRequest(notification_id="not-a-uuid"),
            token=token,
        )

    assert refusal.value.code() is grpc.StatusCode.NOT_FOUND


# -- the rest of the surface, matching the endpoints one for one ------------

CHANGES = ["Read", "Unread", "Dismiss", "Restore"]


def _request(call: str, notification_id: str) -> Any:
    """The per-notification request message for one call. All the same shape."""
    return getattr(notifications_pb2, f"{call}Request")(notification_id=notification_id)


@pytest.mark.parametrize("call", CHANGES)
def test_every_change_needs_a_credential(
    call: str, mail: tuple[Notification, Notification], grpc_call: Callable[..., Any]
) -> None:
    with pytest.raises(grpc.aio.AioRpcError) as refusal:
        grpc_call(Stub, call, _request(call, str(mail[1].pk)))

    assert refusal.value.code() is grpc.StatusCode.UNAUTHENTICATED


@pytest.mark.parametrize("call", CHANGES)
def test_every_change_answers_the_same_shape(
    call: str,
    mail: tuple[Notification, Notification],
    token: str,
    grpc_call: Callable[..., Any],
) -> None:
    reply = grpc_call(Stub, call, _request(call, str(mail[1].pk)), token=token)

    assert reply.id == str(mail[1].pk)
    assert reply.unread >= 0


@pytest.mark.parametrize("call", CHANGES)
def test_every_change_refuses_an_id_that_is_not_one(
    call: str, token: str, grpc_call: Callable[..., Any]
) -> None:
    with pytest.raises(grpc.aio.AioRpcError) as refusal:
        grpc_call(Stub, call, _request(call, "not-a-uuid"), token=token)

    assert refusal.value.code() is grpc.StatusCode.NOT_FOUND


def test_reading_reports_the_new_badge_number(
    mail: tuple[Notification, Notification], token: str, grpc_call: Callable[..., Any]
) -> None:
    reply = grpc_call(Stub, "Read", _request("Read", str(mail[1].pk)), token=token)

    assert (reply.unread, reply.changed) == (1, True)


def test_repeating_a_change_is_success_with_changed_false(
    mail: tuple[Notification, Notification], token: str, grpc_call: Callable[..., Any]
) -> None:
    grpc_call(Stub, "Read", _request("Read", str(mail[1].pk)), token=token)
    reply = grpc_call(Stub, "Read", _request("Read", str(mail[1].pk)), token=token)

    assert reply.changed is False


def test_unreading_puts_it_back_in_the_badge(
    mail: tuple[Notification, Notification], token: str, grpc_call: Callable[..., Any]
) -> None:
    grpc_call(Stub, "Read", _request("Read", str(mail[1].pk)), token=token)
    reply = grpc_call(Stub, "Unread", _request("Unread", str(mail[1].pk)), token=token)

    assert reply.unread == 2


def test_one_notification_can_be_fetched_by_id(
    mail: tuple[Notification, Notification], token: str, grpc_call: Callable[..., Any]
) -> None:
    reply = grpc_call(
        Stub, "Get", notifications_pb2.GetRequest(notification_id=str(mail[1].pk)), token=token
    )

    assert reply.subject == mail[1].subject
    assert reply.dismissed is False


def test_fetching_somebody_elses_notification_is_not_found(
    token: str, grpc_call: Callable[..., Any], alice: Any
) -> None:
    other = get_user_model().objects.create_user(username="bob", email="bob@example.test")
    theirs = Notification.objects.create(
        audience=Audience.USER, recipient=other, subject="For bob only"
    )

    with pytest.raises(grpc.aio.AioRpcError) as refusal:
        grpc_call(
            Stub,
            "Get",
            notifications_pb2.GetRequest(notification_id=str(theirs.pk)),
            token=token,
        )

    assert refusal.value.code() is grpc.StatusCode.NOT_FOUND


def test_the_list_says_how_much_there_was_to_page_through(
    mail: tuple[Notification, Notification], token: str, grpc_call: Callable[..., Any]
) -> None:
    reply = grpc_call(Stub, "List", notifications_pb2.ListRequest(limit=1), token=token)

    assert (len(reply.notifications), reply.total, reply.limit) == (1, 2, 1)


def test_the_list_can_be_narrowed_by_audience(
    mail: tuple[Notification, Notification], token: str, grpc_call: Callable[..., Any]
) -> None:
    reply = grpc_call(Stub, "List", notifications_pb2.ListRequest(audience="global"), token=token)

    assert [row.subject for row in reply.notifications] == [mail[0].subject]


def test_the_unread_flag_is_three_valued(
    mail: tuple[Notification, Notification], token: str, grpc_call: Callable[..., Any]
) -> None:
    """Unset is "everything", which is a different question from `unread = false`."""
    grpc_call(Stub, "Read", _request("Read", str(mail[1].pk)), token=token)

    everything = grpc_call(Stub, "List", notifications_pb2.ListRequest(), token=token)
    read = grpc_call(Stub, "List", notifications_pb2.ListRequest(unread=False), token=token)

    assert len(everything.notifications) == 2
    assert [row.subject for row in read.notifications] == [mail[1].subject]


def test_dismissed_rows_are_left_out_unless_asked_for(
    mail: tuple[Notification, Notification], token: str, grpc_call: Callable[..., Any]
) -> None:
    grpc_call(Stub, "Dismiss", _request("Dismiss", str(mail[1].pk)), token=token)

    hidden = grpc_call(Stub, "List", notifications_pb2.ListRequest(), token=token)
    shown = grpc_call(
        Stub, "List", notifications_pb2.ListRequest(include_dismissed=True), token=token
    )

    assert [row.subject for row in hidden.notifications] == [mail[0].subject]
    assert len(shown.notifications) == 2


def test_the_count_call_answers_a_filtered_total(
    mail: tuple[Notification, Notification], token: str, grpc_call: Callable[..., Any]
) -> None:
    reply = grpc_call(Stub, "Count", notifications_pb2.CountRequest(audience="global"), token=token)

    assert reply.count == 1


def test_the_count_call_needs_a_credential(
    transactional_db: None, grpc_call: Callable[..., Any]
) -> None:
    with pytest.raises(grpc.aio.AioRpcError) as refusal:
        grpc_call(Stub, "Count", notifications_pb2.CountRequest())

    assert refusal.value.code() is grpc.StatusCode.UNAUTHENTICATED


def test_the_badge_is_readable_on_its_own(
    mail: tuple[Notification, Notification], token: str, grpc_call: Callable[..., Any]
) -> None:
    assert grpc_call(Stub, "UnreadCount", Empty(), token=token).count == 2


def test_emptying_the_tray_clears_the_list_and_the_badge(
    mail: tuple[Notification, Notification], token: str, grpc_call: Callable[..., Any]
) -> None:
    reply = grpc_call(Stub, "DismissAll", Empty(), token=token)

    assert (reply.count, reply.unread) == (2, 0)
    assert grpc_call(Stub, "List", notifications_pb2.ListRequest(), token=token).notifications == []


def test_emptying_the_tray_needs_a_credential(
    transactional_db: None, grpc_call: Callable[..., Any]
) -> None:
    with pytest.raises(grpc.aio.AioRpcError) as refusal:
        grpc_call(Stub, "DismissAll", Empty())

    assert refusal.value.code() is grpc.StatusCode.UNAUTHENTICATED
