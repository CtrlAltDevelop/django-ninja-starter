from urllib.parse import parse_qs, urlparse

import pytest
from django.conf import settings
from django.contrib.auth import get_user_model
from django.test import Client

from infrastructure.oauth_core.crypto import decrypt_secret, encrypt_secret
from infrastructure.oauth_core.models import SocialLoginAttempt
from infrastructure.oauth_core.social import ProviderTokens, SocialProfile
from infrastructure.oauth_google.models import GoogleAccount
from infrastructure.oauth_google.provider import provider

pytestmark = pytest.mark.django_db


def _configure_google(monkeypatch: pytest.MonkeyPatch) -> None:
    configuration = {
        **settings.OAUTH_PROVIDER_CONFIG["google"],
        "client_id": "google-client",
        "client_secret": "google-secret",
    }
    monkeypatch.setitem(settings.OAUTH_PROVIDER_CONFIG, "google", configuration)


def test_google_start_creates_one_time_state_and_pkce(monkeypatch: pytest.MonkeyPatch) -> None:
    _configure_google(monkeypatch)
    response = Client().get("/api/v1/oauth/google/start", {"next": "/dashboard"})

    assert response.status_code == 302
    query = parse_qs(urlparse(response.headers["Location"]).query)
    assert query["client_id"] == ["google-client"]
    assert query["response_type"] == ["code"]
    assert query["code_challenge_method"] == ["S256"]
    assert "state" in query
    attempt = SocialLoginAttempt.objects.get()
    assert attempt.next_url == "/dashboard"
    assert attempt.state_hash != query["state"][0]
    assert decrypt_secret(attempt.code_verifier_encrypted)


def test_unconfigured_provider_returns_service_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setitem(
        settings.OAUTH_PROVIDER_CONFIG,
        "google",
        {**settings.OAUTH_PROVIDER_CONFIG["google"], "client_id": "", "client_secret": ""},
    )
    response = Client().get("/api/v1/oauth/google/start")

    assert response.status_code == 503
    assert response.json()["detail"] == "google OAuth is not configured"


def test_callback_creates_account_logs_in_and_rejects_replay(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configure_google(monkeypatch)
    client = Client()
    start = client.get("/api/v1/oauth/google/start", {"next": "/dashboard"})
    state = parse_qs(urlparse(start.headers["Location"]).query)["state"][0]

    monkeypatch.setattr(
        provider,
        "complete",
        lambda **kwargs: (
            ProviderTokens(
                access_token="provider-access",
                refresh_token="provider-refresh",
                expires_in=3600,
                scopes=["openid", "email"],
            ),
            SocialProfile(
                subject="google-subject",
                email="user@example.com",
                email_verified=True,
                display_name="Example User",
                claims={"sub": "google-subject", "hd": "example.com"},
            ),
        ),
    )
    response = client.get(
        "/api/v1/oauth/google/callback",
        {"state": state, "code": "authorization-code"},
    )

    assert response.status_code == 302
    assert response.headers["Location"] == "/dashboard"
    account = GoogleAccount.objects.select_related("user").get(subject="google-subject")
    assert account.email_verified
    assert account.hosted_domain == "example.com"
    assert account.access_token_encrypted == ""
    assert str(account.user_id) == client.session["_auth_user_id"]

    replay = client.get(
        "/api/v1/oauth/google/callback",
        {"state": state, "code": "authorization-code"},
    )
    assert replay.status_code == 400
    assert "already used" in replay.json()["detail"]


def test_social_login_does_not_link_an_existing_user_by_email(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configure_google(monkeypatch)
    existing = get_user_model().objects.create_user(username="existing", email="same@example.com")
    client = Client()
    start = client.get("/api/v1/oauth/google/start")
    state = parse_qs(urlparse(start.headers["Location"]).query)["state"][0]
    monkeypatch.setattr(
        provider,
        "complete",
        lambda **kwargs: (
            ProviderTokens(access_token="access"),
            SocialProfile(
                subject="different-subject",
                email="same@example.com",
                email_verified=True,
            ),
        ),
    )

    response = client.get(
        "/api/v1/oauth/google/callback",
        {"state": state, "code": "authorization-code"},
    )

    account = GoogleAccount.objects.get(subject="different-subject")
    assert response.status_code == 302
    assert account.user_id != existing.pk


def test_provider_credentials_can_be_encrypted_and_decrypted() -> None:
    encrypted = encrypt_secret("upstream-refresh-token")

    assert encrypted != "upstream-refresh-token"
    assert decrypt_secret(encrypted) == "upstream-refresh-token"


def test_provider_error_is_recorded_on_consumed_attempt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configure_google(monkeypatch)
    client = Client()
    start = client.get("/api/v1/oauth/google/start")
    state = parse_qs(urlparse(start.headers["Location"]).query)["state"][0]

    response = client.get(
        "/api/v1/oauth/google/callback",
        {"state": state, "error": "access_denied"},
    )

    attempt = SocialLoginAttempt.objects.get()
    assert response.status_code == 400
    assert attempt.consumed_at is not None
    assert attempt.error == "access_denied"
