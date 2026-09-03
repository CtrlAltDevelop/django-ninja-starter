"""Password authentication over GraphQL.

One service, three doors: what is checked here is that the door does not change
the answer -- the same refusals, with the same `title`, and a credential the
REST endpoints accept.
"""

import json
from typing import Any

import pytest
from django.test import Client

PASSWORD = "corr3ct-horse-battery"
SIGNUP = """
mutation($identifier: String!, $password: String!, $email: String!) {
  passwordSignup(identifier: $identifier, password: $password, email: $email) {
    requiresSecondFactor
    credentials { tokenType accessToken }
  }
}
"""
LOGIN = """
mutation($identifier: String!, $password: String!) {
  passwordLogin(identifier: $identifier, password: $password) {
    requiresSecondFactor
    credentials { accessToken }
  }
}
"""
CHANGE = """
mutation($current: String!, $new: String!) {
  passwordChange(currentPassword: $current, newPassword: $new) { detail }
}
"""
FORGOT = """
mutation($email: String!) { passwordForgot(email: $email) { detail ticket expiresIn } }
"""


def graphql(query: str, token: str | None = None, **variables: Any) -> dict[str, Any]:
    headers = {"HTTP_AUTHORIZATION": f"Bearer {token}"} if token else {}
    response = Client().post(
        "/graphql",
        data={"query": query, "variables": variables},
        content_type="application/json",
        **headers,
    )
    assert response.status_code == 200, response.content
    return json.loads(response.content)


@pytest.fixture
def signed_up(db: None) -> str:
    body = graphql(SIGNUP, identifier="zoe", password=PASSWORD, email="zoe@example.com")
    token: str = body["data"]["passwordSignup"]["credentials"]["accessToken"]
    return token


def test_signup_issues_a_credential_the_rest_api_also_accepts(signed_up: str) -> None:
    """The point of one issuer: a token minted here opens the REST routes."""
    response = Client().get("/api/v1/users/me", HTTP_AUTHORIZATION=f"Bearer {signed_up}")

    assert response.status_code == 200
    assert response.json()["data"]["username"] == "zoe"


def test_login_accepts_the_password_signup_set(signed_up: str) -> None:
    body = graphql(LOGIN, identifier="zoe", password=PASSWORD)

    assert body["data"]["passwordLogin"]["requiresSecondFactor"] is False
    assert body["data"]["passwordLogin"]["credentials"]["accessToken"]


def test_a_wrong_password_is_refused_with_the_same_title_rest_uses(signed_up: str) -> None:
    body = graphql(LOGIN, identifier="zoe", password="not-the-password")

    assert body["errors"][0]["extensions"]["title"] == "INVALID_CREDENTIALS"
    assert body["errors"][0]["extensions"]["status"] == 401


def test_forgot_answers_the_same_for_an_address_with_no_account(db: None) -> None:
    known = graphql(FORGOT, email="nobody@example.com")["data"]["passwordForgot"]

    assert known["detail"] == "If that address has an account, a reset code is on its way."
    assert known["ticket"]


def test_a_wrong_current_password_is_a_400_here_too(signed_up: str) -> None:
    """Not a 401: the caller is authenticated, they just mistyped."""
    body = graphql(CHANGE, token=signed_up, current="wrong", new="another-good-passw0rd")

    assert body["errors"][0]["extensions"]["status"] == 400
    assert body["errors"][0]["extensions"]["title"] == "INCORRECT_PASSWORD"


def test_changing_a_password_needs_a_credential(db: None) -> None:
    body = graphql(CHANGE, current=PASSWORD, new="another-good-passw0rd")

    assert body["errors"][0]["extensions"]["title"] == "AUTHENTICATION_REQUIRED"
