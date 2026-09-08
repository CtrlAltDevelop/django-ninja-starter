from urllib.parse import parse_qs, urlparse

import pytest
from django.conf import settings
from django.contrib.auth import get_user_model
from django.test import Client

from infrastructure.oauth.core.crypto import decrypt_secret, encrypt_secret
from infrastructure.oauth.core.models import SocialLoginAttempt
from infrastructure.oauth.core.social import ProviderTokens, SocialProfile
from infrastructure.oauth.google.models import GoogleAccount
from infrastructure.oauth.google.provider import provider

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
    assert response.json()["description"] == "google OAuth is not configured"


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
    assert "already used" in replay.json()["description"]


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


def test_callback_is_rejected_when_another_browser_presents_the_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """State alone must not log a victim into the account that started the flow."""
    _configure_google(monkeypatch)
    attacker = Client()
    victim = Client()
    start = attacker.get("/api/v1/oauth/google/start")
    state = parse_qs(urlparse(start.headers["Location"]).query)["state"][0]
    monkeypatch.setattr(
        provider,
        "complete",
        lambda **kwargs: (
            ProviderTokens(access_token="access"),
            SocialProfile(subject="attacker-subject", email="attacker@example.com"),
        ),
    )

    response = victim.get(
        "/api/v1/oauth/google/callback",
        {"state": state, "code": "attacker-code"},
    )

    assert response.status_code == 400
    assert "browser" in response.json()["description"]
    assert "_auth_user_id" not in victim.session
    assert SocialLoginAttempt.objects.get().error


def test_start_binds_the_attempt_to_a_same_site_cookie(monkeypatch: pytest.MonkeyPatch) -> None:
    _configure_google(monkeypatch)

    response = Client().get("/api/v1/oauth/google/start")

    cookie = response.cookies["oauth_binding_google"]
    assert cookie.value
    assert cookie["samesite"] == "Lax"
    assert cookie["httponly"]
    assert SocialLoginAttempt.objects.get().binding_hash not in {"", cookie.value}


def test_binding_cookie_is_cleared_once_the_callback_completes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configure_google(monkeypatch)
    client = Client()
    start = client.get("/api/v1/oauth/google/start")
    state = parse_qs(urlparse(start.headers["Location"]).query)["state"][0]
    monkeypatch.setattr(
        provider,
        "complete",
        lambda **kwargs: (
            ProviderTokens(access_token="access"),
            SocialProfile(subject="google-subject"),
        ),
    )

    response = client.get("/api/v1/oauth/google/callback", {"state": state, "code": "code"})

    assert response.status_code == 302
    assert response.cookies["oauth_binding_google"].value == ""


def test_unreadable_verifier_fails_the_callback_cleanly(monkeypatch: pytest.MonkeyPatch) -> None:
    """A rotated encryption key must not surface as an unhandled server error."""
    _configure_google(monkeypatch)
    client = Client()
    start = client.get("/api/v1/oauth/google/start")
    state = parse_qs(urlparse(start.headers["Location"]).query)["state"][0]
    SocialLoginAttempt.objects.update(code_verifier_encrypted="gAAAAABnot-a-fernet-token")

    response = client.get("/api/v1/oauth/google/callback", {"state": state, "code": "code"})

    assert response.status_code == 400
    assert "decrypted" in response.json()["description"]


def test_a_profile_without_a_subject_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    _configure_google(monkeypatch)
    client = Client()
    start = client.get("/api/v1/oauth/google/start")
    state = parse_qs(urlparse(start.headers["Location"]).query)["state"][0]
    monkeypatch.setattr(
        provider,
        "complete",
        lambda **kwargs: (
            ProviderTokens(access_token="access"),
            SocialProfile(subject="", email="nobody@example.com"),
        ),
    )

    response = client.get("/api/v1/oauth/google/callback", {"state": state, "code": "code"})

    assert response.status_code == 400
    assert "subject" in response.json()["description"]
    assert not GoogleAccount.objects.exists()


def test_oversized_profile_values_are_clipped_to_their_columns(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configure_google(monkeypatch)
    client = Client()
    start = client.get("/api/v1/oauth/google/start")
    state = parse_qs(urlparse(start.headers["Location"]).query)["state"][0]
    monkeypatch.setattr(
        provider,
        "complete",
        lambda **kwargs: (
            ProviderTokens(access_token="access"),
            SocialProfile(
                subject="google-subject",
                display_name="N" * 400,
                avatar_url="https://example.com/" + "a" * 1200,
            ),
        ),
    )

    response = client.get("/api/v1/oauth/google/callback", {"state": state, "code": "code"})

    account = GoogleAccount.objects.get(subject="google-subject")
    assert response.status_code == 302
    assert len(account.display_name) == 255
    assert len(account.avatar_url) == 1000


def test_stored_provider_tokens_are_dropped_when_storage_is_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configure_google(monkeypatch)
    monkeypatch.setattr(settings, "OAUTH_STORE_PROVIDER_TOKENS", False)
    client = Client()
    start = client.get("/api/v1/oauth/google/start")
    state = parse_qs(urlparse(start.headers["Location"]).query)["state"][0]
    monkeypatch.setattr(
        provider,
        "complete",
        lambda **kwargs: (
            ProviderTokens(access_token="access", refresh_token="refresh"),
            SocialProfile(subject="google-subject"),
        ),
    )
    GoogleAccount.objects.create(
        user=get_user_model().objects.create_user(username="stale"),
        subject="google-subject",
        access_token_encrypted=encrypt_secret("previously-stored"),
        refresh_token_encrypted=encrypt_secret("previously-stored"),
    )

    client.get("/api/v1/oauth/google/callback", {"state": state, "code": "code"})

    account = GoogleAccount.objects.get(subject="google-subject")
    assert account.access_token_encrypted == ""
    assert account.refresh_token_encrypted == ""


def test_a_disabled_account_cannot_sign_in_through_a_provider(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Deactivating an account has to close the social door too, not just the local ones."""
    _configure_google(monkeypatch)
    user = get_user_model()._default_manager.create_user(username="banned")
    user.is_active = False
    user.save(update_fields=["is_active"])
    GoogleAccount.objects.create(user=user, subject="google-subject")

    client = Client()
    start = client.get("/api/v1/oauth/google/start")
    state = parse_qs(urlparse(start.headers["Location"]).query)["state"][0]
    monkeypatch.setattr(
        provider,
        "complete",
        lambda **kwargs: (
            ProviderTokens(access_token="provider-access"),
            SocialProfile(subject="google-subject", email="banned@example.com"),
        ),
    )

    response = client.get("/api/v1/oauth/google/callback", {"code": "code", "state": state})

    assert response.status_code == 400
    assert response.json()["description"] == "This account is disabled"
    assert "_auth_user_id" not in client.session


def test_a_social_login_can_be_exchanged_for_a_working_api_credential(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The callback leaves a cookie; an API client needs the bearer pair behind it."""
    _configure_google(monkeypatch)
    client = Client()
    start = client.get("/api/v1/oauth/google/start")
    state = parse_qs(urlparse(start.headers["Location"]).query)["state"][0]
    monkeypatch.setattr(
        provider,
        "complete",
        lambda **kwargs: (
            ProviderTokens(access_token="provider-access"),
            SocialProfile(subject="google-subject", email="zoe@example.com", email_verified=True),
        ),
    )
    client.get("/api/v1/oauth/google/callback", {"code": "code", "state": state})
    assert client.get("/api/v1/users/me").status_code == 401, "the cookie alone is not a credential"

    exchanged = client.post("/api/v1/auth/token/exchange")

    assert exchanged.status_code == 200, exchanged.content
    credentials = exchanged.json()["data"]
    assert credentials["token_type"] == "bearer"
    me = Client().get(
        "/api/v1/users/me",
        HTTP_AUTHORIZATION=f"Bearer {credentials['access_token']}",
    )
    assert me.status_code == 200, me.content
    # Not the provider's address: a unique `email` column gets a placeholder, so
    # that a provider claiming somebody else's address cannot annex their account.
    assert me.json()["data"]["id"] == str(GoogleAccount.objects.get().user_id)


def test_the_exchanged_session_does_not_outlive_the_exchange(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """One sign-in must not leave two independent credentials behind."""
    _configure_google(monkeypatch)
    client = Client()
    start = client.get("/api/v1/oauth/google/start")
    state = parse_qs(urlparse(start.headers["Location"]).query)["state"][0]
    monkeypatch.setattr(
        provider,
        "complete",
        lambda **kwargs: (
            ProviderTokens(access_token="provider-access"),
            SocialProfile(subject="google-subject", email="zoe@example.com"),
        ),
    )
    client.get("/api/v1/oauth/google/callback", {"code": "code", "state": state})
    assert client.post("/api/v1/auth/token/exchange").status_code == 200

    assert client.post("/api/v1/auth/token/exchange").status_code == 401


def test_exchanging_without_a_session_is_refused(db: None) -> None:
    assert Client().post("/api/v1/auth/token/exchange").status_code == 401
