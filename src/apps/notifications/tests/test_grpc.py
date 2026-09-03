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
