"""The account, read and updated over gRPC.

The credential travels as ``authorization`` metadata rather than as a header,
and is the same bearer token the other two doors take.
"""

from collections.abc import Callable
from typing import Any

import grpc
import pytest
from django.test import Client
from google.protobuf.empty_pb2 import Empty

from infrastructure.accounts.grpc import accounts_pb2, accounts_pb2_grpc

Stub = accounts_pb2_grpc.AccountControllerStub
PASSWORD = "corr3ct-horse-battery"


@pytest.fixture
def token(transactional_db: None) -> str:
    response = Client().post(
        "/api/v1/auth/password/signup",
        {"identifier": "zoe", "password": PASSWORD, "email": "zoe@example.com"},
        content_type="application/json",
    )
    assert response.status_code == 200, response.content
    access: str = response.json()["data"]["credentials"]["access_token"]
    return access


def test_me_describes_the_signed_in_account(token: str, grpc_call: Callable[..., Any]) -> None:
    reply = grpc_call(Stub, "Me", Empty(), token=token)

    assert reply.username == "zoe"
    assert reply.email == "zoe@example.com"
    assert reply.is_staff is False


def test_me_refuses_a_call_carrying_no_credential(
    transactional_db: None, grpc_call: Callable[..., Any]
) -> None:
    """A refusal becomes a status code, and keeps the `title` in its detail."""
    with pytest.raises(grpc.aio.AioRpcError) as refusal:
        grpc_call(Stub, "Me", Empty())

    assert refusal.value.code() is grpc.StatusCode.UNAUTHENTICATED
    assert "AUTHENTICATION_REQUIRED" in refusal.value.details()


def test_update_profile_changes_only_the_fields_the_call_set(
    token: str, grpc_call: Callable[..., Any]
) -> None:
    """`optional` in the proto is what makes an unset field mean "leave it alone"."""
    grpc_call(
        Stub,
        "UpdateProfile",
        accounts_pb2.ProfileUpdate(display_name="Zoe", bio="Hello"),
        token=token,
    )
    reply = grpc_call(Stub, "UpdateProfile", accounts_pb2.ProfileUpdate(bio="Updated"), token=token)

    assert reply.profile.display_name == "Zoe"
    assert reply.profile.bio == "Updated"


def test_an_empty_update_is_refused_the_way_the_other_transports_refuse_it(
    token: str, grpc_call: Callable[..., Any]
) -> None:
    with pytest.raises(grpc.aio.AioRpcError) as refusal:
        grpc_call(Stub, "UpdateProfile", accounts_pb2.ProfileUpdate(), token=token)

    assert refusal.value.code() is grpc.StatusCode.INVALID_ARGUMENT
    assert "VALIDATION_ERROR" in refusal.value.details()
