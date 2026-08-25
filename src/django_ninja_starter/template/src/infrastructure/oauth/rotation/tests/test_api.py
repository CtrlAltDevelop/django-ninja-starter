"""The rotation mode's token endpoints, including what a replayed token costs."""

from typing import Any

from django.contrib.auth import get_user_model
from django.test import Client

from infrastructure.oauth.rotation.models import (
    RefreshTokenReuseEvent,
    RotatingRefreshToken,
    TokenFamily,
)

PASSWORD = "corr3ct-horse-battery"
SIGNUP = "/api/v1/auth/password/signup"
REFRESH = "/api/v1/auth/token/refresh"
REVOKE = "/api/v1/auth/token/revoke"
SESSIONS = "/api/v1/auth/token/sessions"


def _login(client: Client, identifier: str = "zoe") -> dict[str, Any]:
    response = client.post(
        SIGNUP,
        {"identifier": identifier, "password": PASSWORD, "email": f"{identifier}@example.com"},
        content_type="application/json",
    )
    assert response.status_code == 200, response.content
    credentials: dict[str, Any] = response.json()["credentials"]
    return credentials


def _refresh(client: Client, refresh_token: str) -> Any:
    return client.post(REFRESH, {"refresh_token": refresh_token}, content_type="application/json")


def test_refreshing_returns_a_new_pair(db: None) -> None:
    client = Client()
    original = _login(client)

    response = _refresh(client, original["refresh_token"])

    assert response.status_code == 200, response.content
    fresh = response.json()
    assert fresh["access_token"] != original["access_token"]
    assert fresh["refresh_token"] != original["refresh_token"]
    assert fresh["session_id"] == original["session_id"]


def test_a_refreshed_access_token_authenticates(db: None) -> None:
    client = Client()
    fresh = _refresh(client, _login(client)["refresh_token"]).json()

    response = client.get(SESSIONS, HTTP_AUTHORIZATION=f"Bearer {fresh['access_token']}")

    assert response.status_code == 200, response.content
    assert response.json()["mode"] == "rotation"


def test_rotation_records_its_ancestry(db: None) -> None:
    client = Client()
    _refresh(client, _login(client)["refresh_token"])

    tokens = list(RotatingRefreshToken.objects.order_by("rotation_index"))

    assert [token.rotation_index for token in tokens] == [0, 1]
    assert tokens[0].used_at is not None
    assert tokens[0].replaced_by_id == tokens[1].pk
    assert tokens[1].parent_id == tokens[0].pk


def test_a_spent_refresh_token_cannot_be_spent_twice(db: None) -> None:
    client = Client()
    original = _login(client)
    _refresh(client, original["refresh_token"])

    response = _refresh(client, original["refresh_token"])

    assert response.status_code == 401
    assert "security" in response.json()["detail"]


def test_replaying_a_spent_token_ends_the_whole_family(db: None) -> None:
    """The thief and the real client are indistinguishable, so both are cut off."""
    client = Client()
    original = _login(client)
    successor = _refresh(client, original["refresh_token"]).json()

    _refresh(client, original["refresh_token"])

    family = TokenFamily.objects.get()
    assert family.reuse_detected_at is not None
    assert family.revoked_at is not None
    assert RefreshTokenReuseEvent.objects.count() == 1
    assert _refresh(client, successor["refresh_token"]).status_code == 401
    assert (
        client.get(SESSIONS, HTTP_AUTHORIZATION=f"Bearer {successor['access_token']}").status_code
        == 401
    )


def test_a_reuse_event_keeps_only_a_fingerprint_of_the_digest(db: None) -> None:
    client = Client()
    original = _login(client)
    _refresh(client, original["refresh_token"])
    _refresh(client, original["refresh_token"])

    event = RefreshTokenReuseEvent.objects.get()

    assert len(event.presented_fingerprint) == 16
    assert event.refresh_token is not None
    assert event.presented_fingerprint == event.refresh_token.token_hash[:16]


def test_refreshing_needs_a_token(db: None) -> None:
    assert _refresh(Client(), "").status_code == 400


def test_an_unsigned_refresh_token_is_refused(db: None) -> None:
    assert _refresh(Client(), "not-a-jwt").status_code == 401


def test_an_access_token_is_not_accepted_at_the_refresh_endpoint(db: None) -> None:
    client = Client()
    credentials = _login(client)

    assert _refresh(client, credentials["access_token"]).status_code == 401


def test_refreshing_after_a_revoked_session_is_refused(db: None) -> None:
    client = Client()
    credentials = _login(client)
    client.post(REVOKE, {"token": credentials["refresh_token"]}, content_type="application/json")

    response = _refresh(client, credentials["refresh_token"])

    assert response.status_code == 401
    assert "Sign in again" in response.json()["detail"]


def test_revoking_reports_success_even_for_an_unknown_token(db: None) -> None:
    """A client signing out should never be told its logout failed."""
    response = Client().post(REVOKE, {"token": "not-a-jwt"}, content_type="application/json")

    assert response.status_code == 200


def test_sessions_lists_what_the_login_created(db: None) -> None:
    client = Client()
    credentials = _login(client)

    body = client.get(SESSIONS, HTTP_AUTHORIZATION=f"Bearer {credentials['access_token']}").json()

    assert [entry["session_id"] for entry in body["sessions"]] == [credentials["session_id"]]
    assert body["sessions"][0]["auth_method"] == "password"


def test_sessions_needs_a_credential(db: None) -> None:
    assert Client().get(SESSIONS).status_code == 401


def test_ending_a_session_stops_its_tokens_working(db: None) -> None:
    client = Client()
    credentials = _login(client)
    header = {"HTTP_AUTHORIZATION": f"Bearer {credentials['access_token']}"}

    response = client.delete(f"{SESSIONS}/{credentials['session_id']}", **header)

    assert response.status_code == 200, response.content
    assert client.get(SESSIONS, **header).status_code == 401


def test_one_account_cannot_end_another_accounts_session(db: None) -> None:
    victim = _login(Client(), "victim")
    attacker_client = Client()
    attacker = _login(attacker_client, "attacker")

    response = attacker_client.delete(
        f"{SESSIONS}/{victim['session_id']}",
        HTTP_AUTHORIZATION=f"Bearer {attacker['access_token']}",
    )

    assert response.status_code == 404
    assert TokenFamily.objects.get(pk=victim["session_id"]).revoked_at is None


def test_ending_an_unknown_session_is_a_not_found(db: None) -> None:
    client = Client()
    credentials = _login(client)

    response = client.delete(
        f"{SESSIONS}/{TokenFamily.objects.get().pk}".replace(
            str(TokenFamily.objects.get().pk), "00000000-0000-0000-0000-000000000000"
        ),
        HTTP_AUTHORIZATION=f"Bearer {credentials['access_token']}",
    )

    assert response.status_code == 404


def test_only_the_owning_account_sees_its_sessions(db: None) -> None:
    get_user_model()._default_manager.create_user(username="other")
    client = Client()
    credentials = _login(client, "zoe")
    _login(Client(), "someone-else")

    body = client.get(SESSIONS, HTTP_AUTHORIZATION=f"Bearer {credentials['access_token']}").json()

    assert len(body["sessions"]) == 1
