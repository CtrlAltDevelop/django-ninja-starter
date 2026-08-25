"""The sliding mode's token endpoints.

This mode has no second token, so refreshing means "push the idle deadline out
and tell me what is left" rather than "trade one credential for another".
"""

from datetime import timedelta
from typing import Any

import pytest
from django.test import Client
from django.utils import timezone

from infrastructure.oauth.sliding.models import SlidingToken, SlidingTokenEvent

PASSWORD = "corr3ct-horse-battery"
SIGNUP = "/api/v1/auth/password/signup"
REFRESH = "/api/v1/auth/token/refresh"
REVOKE = "/api/v1/auth/token/revoke"
SESSIONS = "/api/v1/auth/token/sessions"

pytestmark = pytest.mark.urls("infrastructure.oauth.sliding.tests.urls")


@pytest.fixture(autouse=True)
def _sliding_mode(settings: Any) -> None:
    """Pin the mode so credentials are issued into the tables under test."""
    settings.AUTH_TOKEN_MODE = "sliding"


def _login(client: Client, identifier: str = "zoe") -> dict[str, Any]:
    response = client.post(
        SIGNUP,
        {"identifier": identifier, "password": PASSWORD, "email": f"{identifier}@example.com"},
        content_type="application/json",
    )
    assert response.status_code == 200, response.content
    credentials: dict[str, Any] = response.json()["credentials"]
    return credentials


def test_a_login_issues_one_token_and_no_refresh_token(db: None) -> None:
    credentials = _login(Client())

    assert credentials["access_token"]
    assert credentials["refresh_token"] == ""


def test_refreshing_pushes_the_idle_deadline_out(db: None) -> None:
    client = Client()
    credentials = _login(client)
    token = SlidingToken.objects.get()
    SlidingToken.objects.filter(pk=token.pk).update(
        expires_at=timezone.now() + timedelta(seconds=60)
    )

    response = client.post(
        REFRESH,
        {"refresh_token": credentials["access_token"]},
        content_type="application/json",
    )

    assert response.status_code == 200, response.content
    token.refresh_from_db()
    assert token.expires_at > timezone.now() + timedelta(seconds=60)
    assert response.json()["expires_in"] > 60


def test_refreshing_returns_the_same_token_value(db: None) -> None:
    """Its signature already covers the absolute lifetime, so only the row moves."""
    client = Client()
    credentials = _login(client)

    fresh = client.post(
        REFRESH,
        {"refresh_token": credentials["access_token"]},
        content_type="application/json",
    ).json()

    assert fresh["refresh_token"] == ""
    assert (
        client.get(SESSIONS, HTTP_AUTHORIZATION=f"Bearer {fresh['access_token']}").status_code
        == 200
    )


def test_refreshing_falls_back_to_the_authorization_header(db: None) -> None:
    client = Client()
    credentials = _login(client)

    response = client.post(
        REFRESH,
        {},
        content_type="application/json",
        HTTP_AUTHORIZATION=f"Bearer {credentials['access_token']}",
    )

    assert response.status_code == 200, response.content


def test_each_slide_is_recorded(db: None) -> None:
    client = Client()
    credentials = _login(client)

    client.post(
        REFRESH,
        {"refresh_token": credentials["access_token"]},
        content_type="application/json",
    )

    assert SlidingTokenEvent.objects.filter(event_type="slid").count() == 1
    assert SlidingTokenEvent.objects.filter(event_type="issued").count() == 1


def test_refreshing_a_revoked_token_is_refused(db: None) -> None:
    client = Client()
    credentials = _login(client)
    SlidingToken.objects.get().revoke("logout")

    response = client.post(
        REFRESH,
        {"refresh_token": credentials["access_token"]},
        content_type="application/json",
    )

    assert response.status_code == 401
    assert "Sign in again" in response.json()["detail"]


def test_a_token_past_its_absolute_lifetime_cannot_slide(db: None) -> None:
    """The whole point of the absolute bound is that sliding cannot outrun it."""
    client = Client()
    credentials = _login(client)
    SlidingToken.objects.filter(pk=SlidingToken.objects.get().pk).update(
        absolute_expires_at=timezone.now() - timedelta(seconds=1)
    )

    response = client.post(
        REFRESH,
        {"refresh_token": credentials["access_token"]},
        content_type="application/json",
    )

    assert response.status_code == 401


def test_an_unsigned_token_is_refused(db: None) -> None:
    response = Client().post(
        REFRESH, {"refresh_token": "not-a-jwt"}, content_type="application/json"
    )

    assert response.status_code == 401


def test_revoking_a_listed_token_stops_it_working(db: None) -> None:
    client = Client()
    credentials = _login(client)
    header = {"HTTP_AUTHORIZATION": f"Bearer {credentials['access_token']}"}
    body = client.get(SESSIONS, **header).json()

    assert body["mode"] == "sliding"
    assert (
        client.delete(f"{SESSIONS}/{body['sessions'][0]['session_id']}", **header).status_code
        == 200
    )

    assert client.get(SESSIONS, **header).status_code == 401
    assert SlidingTokenEvent.objects.filter(event_type="revoked").count() == 1


def test_revoking_reports_success_even_for_an_unknown_token(db: None) -> None:
    """A client signing out should never be told its logout failed."""
    response = Client().post(REVOKE, {"token": "not-a-jwt"}, content_type="application/json")

    assert response.status_code == 200


def test_ending_an_unknown_session_is_a_not_found(db: None) -> None:
    client = Client()
    credentials = _login(client)

    response = client.delete(
        f"{SESSIONS}/00000000-0000-0000-0000-000000000000",
        HTTP_AUTHORIZATION=f"Bearer {credentials['access_token']}",
    )

    assert response.status_code == 404


def test_a_signed_token_for_a_row_that_no_longer_exists_is_refused(db: None) -> None:
    """The signature is ours, but there is nothing left behind it."""
    client = Client()
    credentials = _login(client)
    SlidingToken.objects.all().delete()

    response = client.post(
        REFRESH,
        {"refresh_token": credentials["access_token"]},
        content_type="application/json",
    )

    assert response.status_code == 401
    assert "not valid" in response.json()["detail"]
