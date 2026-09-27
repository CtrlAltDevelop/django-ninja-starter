"""CSRF is owed by the session cookie, never by a bearer token.

A browser attaches the session cookie to every request on its own, so a write
it authenticates has to prove it came from one of our pages. A bearer token is
only ever sent on purpose, so demanding a CSRF token from it would lock every
non-browser client out. REST and GraphQL must agree on both halves.

Django's test client skips CSRF by default, which is exactly how the GraphQL
half once broke unnoticed -- so every client here enforces it.
"""

from typing import Any

import pytest
from django.contrib.auth import get_user_model
from django.test import Client

PASSWORD = "corr3ct-horse-battery"
CSRF_SECRET = "a" * 32
PROFILE = "/api/v1/users/me/profile"
ME = {"query": "{ me { username } }"}


@pytest.fixture
def session_client(db: None, settings: Any) -> Client:
    settings.AUTH_TOKEN_MODE = "none"
    user = get_user_model().objects.create_user(username="cookie", password=PASSWORD)
    client = Client(enforce_csrf_checks=True)
    client.force_login(user)
    return client


def _with_csrf(client: Client) -> dict[str, str]:
    client.cookies["csrftoken"] = CSRF_SECRET
    return {"HTTP_X_CSRFTOKEN": CSRF_SECRET}


def test_a_bearer_client_can_post_to_graphql(db: None) -> None:
    client = Client(enforce_csrf_checks=True)
    signup = client.post(
        "/api/v1/auth/password/signup",
        {"identifier": "zoe", "password": PASSWORD, "email": "zoe@example.com"},
        content_type="application/json",
    )
    token = signup.json()["data"]["credentials"]["access_token"]

    response = client.post(
        "/graphql", ME, content_type="application/json", HTTP_AUTHORIZATION=f"Bearer {token}"
    )

    assert response.status_code == 200, response.content
    assert response.json()["data"]["me"]["username"] == "zoe"


def test_a_session_write_without_a_token_is_refused_on_rest(session_client: Client) -> None:
    response = session_client.patch(
        PROFILE, {"display_name": "pwned"}, content_type="application/json"
    )

    assert response.status_code == 403


def test_a_session_post_without_a_token_is_refused_on_graphql(session_client: Client) -> None:
    response = session_client.post("/graphql", ME, content_type="application/json")

    assert response.status_code == 403


def test_a_session_write_with_a_token_is_allowed_on_rest(session_client: Client) -> None:
    response = session_client.patch(
        PROFILE,
        {"display_name": "Cookie"},
        content_type="application/json",
        **_with_csrf(session_client),
    )

    assert response.status_code == 200, response.content


def test_a_session_post_with_a_token_is_allowed_on_graphql(session_client: Client) -> None:
    response = session_client.post(
        "/graphql", ME, content_type="application/json", **_with_csrf(session_client)
    )

    assert response.status_code == 200, response.content
    assert response.json()["data"]["me"]["username"] == "cookie"
