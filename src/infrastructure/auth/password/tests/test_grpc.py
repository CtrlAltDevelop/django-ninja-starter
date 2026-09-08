"""Password authentication over gRPC.

Driven through a real asyncio server, so what is exercised is the generated stub
and the registered servicer. `transactional_db` throughout: the server answers on
its own connection and cannot see rows still sitting in a test's transaction.
"""

from collections.abc import Callable
from typing import Any

import grpc
import pytest
from django.test import Client

from infrastructure.auth.password.grpc import auth_password_pb2, auth_password_pb2_grpc

Stub = auth_password_pb2_grpc.PasswordControllerStub
PASSWORD = "corr3ct-horse-battery"


@pytest.fixture
def token(transactional_db: None, grpc_call: Callable[..., Any]) -> str:
    reply = grpc_call(
        Stub,
        "Signup",
        auth_password_pb2.SignupRequest(
            identifier="zoe", password=PASSWORD, email="zoe@example.com"
        ),
    )
    assert reply.requires_second_factor is False
    access: str = reply.credentials.access_token
    return access


def test_a_credential_minted_over_grpc_opens_the_rest_api(token: str) -> None:
    """One issuer, three doors: the token does not remember which one it came from."""
    response = Client().get("/api/v1/users/me", HTTP_AUTHORIZATION=f"Bearer {token}")

    assert response.status_code == 200
    assert response.json()["data"]["username"] == "zoe"


def test_login_accepts_the_password_signup_set(token: str, grpc_call: Callable[..., Any]) -> None:
    reply = grpc_call(
        Stub, "Login", auth_password_pb2.LoginRequest(identifier="zoe", password=PASSWORD)
    )

    assert reply.credentials.access_token


def test_a_wrong_password_is_unauthenticated_here(
    token: str, grpc_call: Callable[..., Any]
) -> None:
    with pytest.raises(grpc.aio.AioRpcError) as refusal:
        grpc_call(Stub, "Login", auth_password_pb2.LoginRequest(identifier="zoe", password="nope"))

    assert refusal.value.code() is grpc.StatusCode.UNAUTHENTICATED
    assert "INVALID_CREDENTIALS" in refusal.value.details()


def test_changing_a_password_needs_a_credential(
    transactional_db: None, grpc_call: Callable[..., Any]
) -> None:
    with pytest.raises(grpc.aio.AioRpcError) as refusal:
        grpc_call(
            Stub,
            "Change",
            auth_password_pb2.ChangeRequest(current_password="a", new_password="b"),
        )

    assert refusal.value.code() is grpc.StatusCode.UNAUTHENTICATED


def test_forgot_answers_the_same_for_an_address_with_no_account(
    transactional_db: None, grpc_call: Callable[..., Any]
) -> None:
    reply = grpc_call(Stub, "Forgot", auth_password_pb2.ForgotRequest(email="nobody@example.com"))

    assert reply.detail == "If that address has an account, a reset code is on its way."
    assert reply.ticket
