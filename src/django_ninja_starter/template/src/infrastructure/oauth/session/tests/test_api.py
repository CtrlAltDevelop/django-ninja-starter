"""The session mode's token endpoints.

The suite runs under the rotation mode by default, so every test here pins the
mode -- both the settings value and the router mounted at ``/auth/token``.
"""

from typing import Any

import pytest
from django.test import Client

from infrastructure.oauth.session.models import OAuthSession, SessionAccessToken, SessionRevocation

PASSWORD = "corr3ct-horse-battery"
SIGNUP = "/api/v1/auth/password/signup"
REFRESH = "/api/v1/auth/token/refresh"
REVOKE = "/api/v1/auth/token/revoke"
SESSIONS = "/api/v1/auth/token/sessions"

pytestmark = pytest.mark.urls("infrastructure.oauth.session.tests.urls")


@pytest.fixture(autouse=True)
def _session_mode(settings: Any) -> None:
    """Pin the mode so credentials are issued into the tables under test."""
    settings.AUTH_TOKEN_MODE = "session"


def _login(client: Client, identifier: str = "zoe") -> dict[str, Any]:
    response = client.post(
        SIGNUP,
        {"identifier": identifier, "password": PASSWORD, "email": f"{identifier}@example.com"},
        content_type="application/json",
    )
    assert response.status_code == 200, response.content
    credentials: dict[str, Any] = response.json()["credentials"]
    return credentials


def test_refreshing_mints_a_new_access_token_for_the_same_session(db: None) -> None:
    client = Client()
    original = _login(client)

    response = client.post(
        REFRESH, {"refresh_token": original["refresh_token"]}, content_type="application/json"
    )

    assert response.status_code == 200, response.content
    fresh = response.json()
    assert fresh["access_token"] != original["access_token"]
    assert fresh["session_id"] == original["session_id"]
    assert SessionAccessToken.objects.count() == 2


def test_the_session_key_is_handed_straight_back(db: None) -> None:
    """It is the long-lived half and is deliberately not rotated in this mode."""
    client = Client()
    original = _login(client)

    fresh = client.post(
        REFRESH, {"refresh_token": original["refresh_token"]}, content_type="application/json"
    ).json()

    assert fresh["refresh_token"] == original["refresh_token"]


def test_both_the_old_and_new_access_tokens_still_work(db: None) -> None:
    """Access tokens are independent of each other; only the session gates them."""
    client = Client()
    original = _login(client)
    fresh = client.post(
        REFRESH, {"refresh_token": original["refresh_token"]}, content_type="application/json"
    ).json()

    for token in (original["access_token"], fresh["access_token"]):
        assert client.get(SESSIONS, HTTP_AUTHORIZATION=f"Bearer {token}").status_code == 200


def test_ending_a_session_revokes_every_access_token_under_it(db: None) -> None:
    client = Client()
    original = _login(client)
    client.post(
        REFRESH, {"refresh_token": original["refresh_token"]}, content_type="application/json"
    )
    header = {"HTTP_AUTHORIZATION": f"Bearer {original['access_token']}"}

    assert client.delete(f"{SESSIONS}/{original['session_id']}", **header).status_code == 200

    assert SessionAccessToken.objects.filter(revoked_at__isnull=True).count() == 0
    assert client.get(SESSIONS, **header).status_code == 401


def test_ending_a_session_records_what_it_cost(db: None) -> None:
    client = Client()
    original = _login(client)
    header = {"HTTP_AUTHORIZATION": f"Bearer {original['access_token']}"}

    client.delete(f"{SESSIONS}/{original['session_id']}", **header)

    revocation = SessionRevocation.objects.get()
    assert revocation.access_tokens_revoked == 1
    assert revocation.revoked_by is not None


def test_refreshing_a_revoked_session_is_refused(db: None) -> None:
    client = Client()
    original = _login(client)
    OAuthSession.objects.get().revoke("logout")

    response = client.post(
        REFRESH, {"refresh_token": original["refresh_token"]}, content_type="application/json"
    )

    assert response.status_code == 401
    assert "Sign in again" in response.json()["detail"]


def test_refreshing_needs_a_token(db: None) -> None:
    response = Client().post(REFRESH, {}, content_type="application/json")

    assert response.status_code == 400


def test_an_unsigned_session_key_is_refused(db: None) -> None:
    response = Client().post(
        REFRESH, {"refresh_token": "not-a-jwt"}, content_type="application/json"
    )

    assert response.status_code == 401


def test_sessions_reports_the_active_mode(db: None) -> None:
    client = Client()
    credentials = _login(client)

    body = client.get(SESSIONS, HTTP_AUTHORIZATION=f"Bearer {credentials['access_token']}").json()

    assert body["mode"] == "session"
    assert [entry["session_id"] for entry in body["sessions"]] == [credentials["session_id"]]


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


def test_a_signed_key_for_a_session_that_no_longer_exists_is_refused(db: None) -> None:
    """The signature is ours, but there is nothing left behind it."""
    client = Client()
    original = _login(client)
    OAuthSession.objects.all().delete()

    response = client.post(
        REFRESH, {"refresh_token": original["refresh_token"]}, content_type="application/json"
    )

    assert response.status_code == 401
    assert "not valid" in response.json()["detail"]
