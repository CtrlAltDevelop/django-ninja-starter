"""Credential issuance and revocation, across every supported token mode."""

from typing import Any
from unittest.mock import patch

import pytest
from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser
from django.contrib.sessions.backends.db import SessionStore
from django.core.exceptions import ImproperlyConfigured
from django.test import RequestFactory, override_settings

from infrastructure.auth.core.sessions import (
    bearer_token,
    client_ip,
    credential_handle,
    issue_credentials,
    resolve_request_user,
    revoke_all_for_user,
    revoke_credentials,
    user_agent,
)
from infrastructure.oauth.core import jwt_tokens

TOKEN_MODES = ["sliding", "session", "rotation"]
PAIRED_MODES = ["session", "rotation"]


@pytest.fixture
def user(db: None) -> Any:
    return get_user_model()._default_manager.create_user(username="zoe")


def _request(**headers: str) -> Any:
    """Build a request carrying what session and auth middleware add in real traffic."""
    request = RequestFactory().post("/api/v1/auth/password/login", **headers)
    request.session = SessionStore()
    request.user = AnonymousUser()
    return request


@pytest.mark.parametrize("mode", TOKEN_MODES)
def test_every_mode_issues_a_bearer_token(user: Any, mode: str) -> None:
    with override_settings(AUTH_TOKEN_MODE=mode):
        credentials = issue_credentials(_request(), user, method="password")

    assert credentials.token_type == "bearer"
    assert credentials.access_token
    assert credentials.session_id


@pytest.mark.parametrize("mode", TOKEN_MODES)
def test_an_issued_token_identifies_its_owner(user: Any, mode: str) -> None:
    with override_settings(AUTH_TOKEN_MODE=mode):
        credentials = issue_credentials(_request(), user, method="password")
        request = _request(HTTP_AUTHORIZATION=f"Bearer {credentials.access_token}")

        assert resolve_request_user(request) == user


@pytest.mark.parametrize("mode", TOKEN_MODES)
def test_revoking_a_token_stops_it_authenticating(user: Any, mode: str) -> None:
    with override_settings(AUTH_TOKEN_MODE=mode):
        credentials = issue_credentials(_request(), user, method="password")
        request = _request(HTTP_AUTHORIZATION=f"Bearer {credentials.access_token}")

        assert revoke_credentials(request) is True
        assert resolve_request_user(request) is None


@pytest.mark.parametrize("mode", ["session", "rotation"])
def test_the_long_lived_half_also_revokes_the_whole_grant(user: Any, mode: str) -> None:
    """Signing out with a refresh token must not leave its access token alive."""
    with override_settings(AUTH_TOKEN_MODE=mode):
        credentials = issue_credentials(_request(), user, method="password")

        assert revoke_credentials(_request(), credentials.refresh_token) is True
        access = _request(HTTP_AUTHORIZATION=f"Bearer {credentials.access_token}")
        assert resolve_request_user(access) is None


@pytest.mark.parametrize("mode", TOKEN_MODES)
def test_an_unknown_token_authenticates_nobody(user: Any, mode: str) -> None:
    with override_settings(AUTH_TOKEN_MODE=mode):
        request = _request(HTTP_AUTHORIZATION="Bearer not-a-real-token")

        assert resolve_request_user(request) is None
        assert revoke_credentials(request) is False


@pytest.mark.parametrize("mode", TOKEN_MODES)
def test_a_deactivated_account_stops_authenticating(user: Any, mode: str) -> None:
    with override_settings(AUTH_TOKEN_MODE=mode):
        credentials = issue_credentials(_request(), user, method="password")
        user.is_active = False
        user.save(update_fields=["is_active"])
        request = _request(HTTP_AUTHORIZATION=f"Bearer {credentials.access_token}")

        assert resolve_request_user(request) is None


@override_settings(AUTH_TOKEN_MODE="sliding")
def test_using_a_sliding_token_pushes_its_expiry_out(user: Any) -> None:
    from infrastructure.oauth.sliding.models import SlidingToken

    credentials = issue_credentials(_request(), user, method="password")
    token = SlidingToken.objects.get()
    original = token.expires_at

    resolve_request_user(_request(HTTP_AUTHORIZATION=f"Bearer {credentials.access_token}"))

    token.refresh_from_db()
    assert token.slide_count == 1
    assert token.expires_at >= original


@override_settings(AUTH_TOKEN_MODE="none")
def test_the_none_mode_signs_in_with_a_django_session(user: Any) -> None:
    request = _request()

    credentials = issue_credentials(request, user, method="password")

    assert credentials.token_type == "session"
    assert request.user == user


@override_settings(AUTH_TOKEN_MODE="none")
def test_the_none_mode_signs_out_of_its_session(user: Any) -> None:
    request = _request()
    issue_credentials(request, user, method="password")

    assert revoke_credentials(request) is True
    assert request.user.is_authenticated is False


@override_settings(AUTH_TOKEN_MODE="rotation")
def test_a_request_with_no_token_is_anonymous(user: Any) -> None:
    assert resolve_request_user(_request()) is None
    assert revoke_credentials(_request()) is False


@pytest.mark.parametrize("mode", TOKEN_MODES)
def test_revoking_everything_clears_every_live_credential(user: Any, mode: str) -> None:
    with override_settings(AUTH_TOKEN_MODE=mode):
        first = issue_credentials(_request(), user, method="password")
        second = issue_credentials(_request(), user, method="password")

        assert revoke_all_for_user(user) >= 2

        for credentials in (first, second):
            request = _request(HTTP_AUTHORIZATION=f"Bearer {credentials.access_token}")
            assert resolve_request_user(request) is None


@pytest.mark.parametrize("mode", TOKEN_MODES)
def test_the_access_token_is_a_signed_jwt_describing_the_login(user: Any, mode: str) -> None:
    with override_settings(AUTH_TOKEN_MODE=mode):
        credentials = issue_credentials(_request(), user, method="password")

    claims = jwt_tokens.decode(credentials.access_token, token_type=jwt_tokens.ACCESS)
    assert claims.subject == str(user.pk)
    assert claims.mode == mode
    assert claims.session_id == credentials.session_id
    assert claims.methods == ["password"]


@pytest.mark.parametrize("mode", PAIRED_MODES)
def test_the_refresh_token_is_signed_as_a_refresh_token(user: Any, mode: str) -> None:
    with override_settings(AUTH_TOKEN_MODE=mode):
        credentials = issue_credentials(_request(), user, method="password")

    claims = jwt_tokens.decode(credentials.refresh_token, token_type=jwt_tokens.REFRESH)
    assert claims.session_id == credentials.session_id


@pytest.mark.parametrize("mode", PAIRED_MODES)
def test_a_refresh_token_cannot_be_used_as_a_bearer_credential(user: Any, mode: str) -> None:
    """Otherwise the long-lived half would authenticate every ordinary request."""
    with override_settings(AUTH_TOKEN_MODE=mode):
        credentials = issue_credentials(_request(), user, method="password")
        request = _request(HTTP_AUTHORIZATION=f"Bearer {credentials.refresh_token}")

        assert resolve_request_user(request) is None


@pytest.mark.parametrize("mode", PAIRED_MODES)
def test_signing_out_accepts_the_refresh_token(user: Any, mode: str) -> None:
    """A client whose access token has lapsed still holds this one."""
    with override_settings(AUTH_TOKEN_MODE=mode):
        credentials = issue_credentials(_request(), user, method="password")

        assert revoke_credentials(_request(), credentials.refresh_token) is True

        request = _request(HTTP_AUTHORIZATION=f"Bearer {credentials.access_token}")
        assert resolve_request_user(request) is None


def test_a_token_minted_for_another_mode_is_refused(user: Any) -> None:
    """Its handle belongs to a different table, so looking it up is meaningless."""
    with override_settings(AUTH_TOKEN_MODE="rotation"):
        credentials = issue_credentials(_request(), user, method="password")

    with override_settings(AUTH_TOKEN_MODE="session"):
        request = _request(HTTP_AUTHORIZATION=f"Bearer {credentials.access_token}")
        assert resolve_request_user(request) is None


@override_settings(AUTH_TOKEN_MODE="rotation")
def test_an_unsigned_bearer_value_never_reaches_the_database(user: Any) -> None:
    with patch("infrastructure.auth.core.sessions._user_from_rotation") as resolver:
        assert resolve_request_user(_request(HTTP_AUTHORIZATION="Bearer not-a-jwt")) is None

    resolver.assert_not_called()


@override_settings(AUTH_TOKEN_MODE="rotation")
def test_signing_out_with_an_unreadable_token_reports_nothing_revoked(user: Any) -> None:
    assert credential_handle("not-a-jwt", token_type=jwt_tokens.ACCESS) == ""
    assert revoke_credentials(_request(), "not-a-jwt") is False


@override_settings(AUTH_TOKEN_MODE="sliding")
def test_a_mode_whose_app_is_not_installed_fails_loudly(user: Any) -> None:
    """Better a clear configuration error than a token silently never issued."""
    with (
        patch("infrastructure.auth.core.sessions.app_installed", return_value=False),
        pytest.raises(ImproperlyConfigured, match="oauth_sliding"),
    ):
        issue_credentials(_request(), user, method="password")


@pytest.mark.parametrize(
    ("header", "expected"),
    [
        ("Bearer abc123", "abc123"),
        ("bearer abc123", "abc123"),
        ("Basic abc123", ""),
        ("", ""),
    ],
)
def test_only_bearer_headers_yield_a_token(header: str, expected: str) -> None:
    assert bearer_token(_request(HTTP_AUTHORIZATION=header)) == expected


def test_a_forwarded_client_ip_wins_over_the_socket_peer() -> None:
    request = _request(HTTP_X_FORWARDED_FOR="203.0.113.7, 70.41.3.18")

    assert client_ip(request) == "203.0.113.7"


def test_the_user_agent_is_read_from_the_request() -> None:
    assert user_agent(_request(HTTP_USER_AGENT="probe/1.0")) == "probe/1.0"
