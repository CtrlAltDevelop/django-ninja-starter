from urllib.parse import parse_qs, urlparse

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.serialization import Encoding, NoEncryption, PrivateFormat
from django.conf import settings
from django.test import Client

from config.api import build_apis
from infrastructure.oauth_apple.provider import provider as apple
from infrastructure.oauth_core.social import OAuthProviderError, ProviderTokens
from infrastructure.oauth_core.tokens import hash_token
from infrastructure.oauth_github.provider import provider as github
from infrastructure.oauth_google.provider import provider as google
from infrastructure.oauth_microsoft.provider import provider as microsoft

pytestmark = pytest.mark.django_db


def _configure(monkeypatch: pytest.MonkeyPatch, name: str) -> None:
    configuration = {
        **settings.OAUTH_PROVIDER_CONFIG[name],
        "client_id": f"{name}-client",
        "client_secret": f"{name}-secret",
    }
    if name == "apple":
        configuration.update({"team_id": "TEAMID", "key_id": "KEYID", "private_key": "placeholder"})
    monkeypatch.setitem(settings.OAUTH_PROVIDER_CONFIG, name, configuration)


@pytest.mark.parametrize(
    ("name", "host"),
    [
        ("google", "accounts.google.com"),
        ("apple", "appleid.apple.com"),
        ("microsoft", "login.microsoftonline.com"),
        ("github", "github.com"),
    ],
)
def test_provider_start_routes_are_independently_configurable(
    monkeypatch: pytest.MonkeyPatch,
    name: str,
    host: str,
) -> None:
    _configure(monkeypatch, name)
    response = Client().get(f"/api/v1/oauth/{name}/start")

    assert response.status_code == 302
    location = urlparse(response.headers["Location"])
    assert location.hostname == host
    assert parse_qs(location.query)["client_id"] == [f"{name}-client"]


@pytest.mark.parametrize("name", ["google", "microsoft", "github"])
def test_get_callbacks_require_state(name: str) -> None:
    response = Client().get(f"/api/v1/oauth/{name}/callback")

    assert response.status_code == 400


def test_apple_form_post_callback_requires_state() -> None:
    response = Client().post("/api/v1/oauth/apple/callback")

    assert response.status_code == 400


def test_google_provider_normalizes_verified_profile(monkeypatch: pytest.MonkeyPatch) -> None:
    tokens = ProviderTokens(access_token="access", id_token="identity")
    monkeypatch.setattr(google, "exchange_code", lambda **kwargs: tokens)
    monkeypatch.setattr(
        google,
        "decode_id_token",
        lambda *args, **kwargs: {
            "sub": "google-subject",
            "email": "google@example.com",
            "email_verified": True,
            "name": "Google User",
            "picture": "https://example.com/avatar.png",
        },
    )

    returned_tokens, profile = google.complete(
        code="code",
        redirect_uri="https://example.com/callback",
        code_verifier="verifier",
        nonce_hash="nonce",
        callback_data={},
    )

    assert returned_tokens == tokens
    assert profile.subject == "google-subject"
    assert profile.email_verified


def test_apple_generates_signed_client_secret_and_profile(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    private_key = (
        ec.generate_private_key(ec.SECP256R1())
        .private_bytes(
            Encoding.PEM,
            PrivateFormat.PKCS8,
            NoEncryption(),
        )
        .decode()
    )
    configuration = {
        **settings.OAUTH_PROVIDER_CONFIG["apple"],
        "client_id": "apple-client",
        "team_id": "TEAMID",
        "key_id": "KEYID",
        "private_key": private_key,
    }
    monkeypatch.setitem(settings.OAUTH_PROVIDER_CONFIG, "apple", configuration)
    assert apple.client_credential()

    tokens = ProviderTokens(access_token="access", id_token="identity")
    monkeypatch.setattr(apple, "exchange_code", lambda **kwargs: tokens)
    monkeypatch.setattr(
        apple,
        "decode_id_token",
        lambda *args, **kwargs: {
            "sub": "apple-subject",
            "email": "private@privaterelay.appleid.com",
            "email_verified": "true",
            "is_private_email": "true",
        },
    )
    _, profile = apple.complete(
        code="code",
        redirect_uri="https://example.com/callback",
        code_verifier="",
        nonce_hash="nonce",
        callback_data={"user": '{"name":{"firstName":"A"}}'},
    )

    assert profile.subject == "apple-subject"
    assert profile.email_verified
    assert profile.display_name == "A"
    assert "apple_user" in profile.claims


def test_microsoft_validates_tenant_and_issuer(monkeypatch: pytest.MonkeyPatch) -> None:
    tenant_id = "11111111-1111-1111-1111-111111111111"
    identity_token = jwt.encode({"tid": tenant_id}, key="", algorithm="none")
    tokens = ProviderTokens(access_token="access", id_token=identity_token)
    monkeypatch.setattr(microsoft, "exchange_code", lambda **kwargs: tokens)
    monkeypatch.setattr(
        microsoft,
        "decode_id_token",
        lambda *args, **kwargs: {
            "sub": "microsoft-subject",
            "tid": tenant_id,
            "iss": f"https://login.microsoftonline.com/{tenant_id}/v2.0",
            "preferred_username": "microsoft@example.com",
            "name": "Microsoft User",
        },
    )

    _, profile = microsoft.complete(
        code="code",
        redirect_uri="https://example.com/callback",
        code_verifier="verifier",
        nonce_hash="nonce",
        callback_data={},
    )

    assert profile.subject == "microsoft-subject"
    assert profile.email == "microsoft@example.com"


def test_microsoft_rejects_an_invalid_tenant_claim(monkeypatch: pytest.MonkeyPatch) -> None:
    identity_token = jwt.encode({"tid": "not-a-tenant"}, key="", algorithm="none")
    tokens = ProviderTokens(access_token="access", id_token=identity_token)
    monkeypatch.setattr(microsoft, "exchange_code", lambda **kwargs: tokens)

    with pytest.raises(OAuthProviderError, match="tenant claim"):
        microsoft.complete(
            code="code",
            redirect_uri="https://example.com/callback",
            code_verifier="verifier",
            nonce_hash="nonce",
            callback_data={},
        )


def test_github_normalizes_profile(monkeypatch: pytest.MonkeyPatch) -> None:
    tokens = ProviderTokens(access_token="access")
    monkeypatch.setattr(github, "exchange_code", lambda **kwargs: tokens)
    monkeypatch.setattr(
        github,
        "fetch_json",
        lambda *args, **kwargs: {
            "id": 42,
            "login": "octocat",
            "name": "The Octocat",
            "email": "octocat@github.com",
            "avatar_url": "https://github.com/avatar.png",
        },
    )
    monkeypatch.setattr(
        github,
        "_primary_email",
        lambda access_token: ("verified@github.com", True),
    )

    _, profile = github.complete(
        code="code",
        redirect_uri="https://example.com/callback",
        code_verifier="verifier",
        nonce_hash="",
        callback_data={},
    )

    assert profile.subject == "42"
    assert profile.display_name == "The Octocat"
    assert profile.email == "verified@github.com"
    assert profile.email_verified


def test_id_token_nonce_is_checked(monkeypatch: pytest.MonkeyPatch) -> None:
    jwk_arguments: dict[str, object] = {}

    class SigningKey:
        key = "public-key"

    class JwkClient:
        def __init__(self, url: str, **kwargs: object) -> None:
            self.url = url
            jwk_arguments.update(kwargs)

        def get_signing_key_from_jwt(self, token: str) -> SigningKey:
            return SigningKey()

    monkeypatch.setattr("infrastructure.oauth_core.provider.jwt.PyJWKClient", JwkClient)
    decode_arguments: dict[str, object] = {}

    def decode(*args: object, **kwargs: object) -> dict[str, object]:
        decode_arguments.update(kwargs)
        return {
            "iss": "https://accounts.google.com",
            "sub": "subject",
            "aud": "test-google-client",
            "exp": 9999999999,
            "iat": 1,
            "nonce": "expected-nonce",
        }

    monkeypatch.setattr("infrastructure.oauth_core.provider.jwt.decode", decode)

    claims = google.decode_id_token("token", hash_token("expected-nonce"))
    assert claims["sub"] == "subject"
    assert jwk_arguments["timeout"] == settings.OAUTH_HTTP_TIMEOUT_SECONDS
    assert decode_arguments["leeway"] == settings.OAUTH_CLOCK_SKEW_SECONDS
    assert decode_arguments["options"] == {
        "verify_iss": True,
        "require": ["iss", "sub", "aud", "exp", "iat"],
    }

    with pytest.raises(OAuthProviderError, match="nonce"):
        google.decode_id_token("token", hash_token("different-nonce"))


def test_invalid_apple_private_key_returns_safe_provider_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configure(monkeypatch, "apple")

    with pytest.raises(OAuthProviderError, match="private key"):
        apple.client_credential()


def test_token_exchange_and_profile_request_use_bounded_http_calls(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configure(monkeypatch, "google")
    calls: list[tuple[str, dict[str, object]]] = []

    class Response:
        def __init__(self, body: object) -> None:
            self.body = body

        def raise_for_status(self) -> None:
            return None

        def json(self) -> object:
            return self.body

    def post(url: str, **kwargs: object) -> Response:
        calls.append((url, kwargs))
        return Response(
            {
                "access_token": "access",
                "refresh_token": "refresh",
                "expires_in": 3600,
                "scope": "openid email",
                "id_token": "identity",
            }
        )

    def get(url: str, **kwargs: object) -> Response:
        calls.append((url, kwargs))
        return Response({"sub": "subject"})

    monkeypatch.setattr("infrastructure.oauth_core.provider.httpx.post", post)
    monkeypatch.setattr("infrastructure.oauth_core.provider.httpx.get", get)

    tokens = google.exchange_code(
        code="code",
        redirect_uri="https://example.test/callback",
        code_verifier="verifier",
    )
    profile = google.fetch_json("https://example.test/userinfo", tokens.access_token)

    assert tokens.refresh_token == "refresh"
    assert tokens.expires_in == 3600
    assert profile == {"sub": "subject"}
    assert all(call[1]["timeout"] == settings.OAUTH_HTTP_TIMEOUT_SECONDS for call in calls)


def test_token_exchange_rejects_invalid_lifetime(monkeypatch: pytest.MonkeyPatch) -> None:
    _configure(monkeypatch, "google")

    class Response:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, object]:
            return {"access_token": "access", "expires_in": "not-a-number"}

    monkeypatch.setattr(
        "infrastructure.oauth_core.provider.httpx.post", lambda *args, **kwargs: Response()
    )

    with pytest.raises(OAuthProviderError, match="lifetime"):
        google.exchange_code(
            code="code",
            redirect_uri="https://example.test/callback",
            code_verifier="verifier",
        )


def test_github_selects_primary_verified_email(monkeypatch: pytest.MonkeyPatch) -> None:
    class Response:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> list[dict[str, object]]:
            return [
                {"email": "other@example.com", "primary": False, "verified": True},
                {"email": "primary@example.com", "primary": True, "verified": True},
            ]

    monkeypatch.setattr(
        "infrastructure.oauth_github.provider.httpx.get", lambda *args, **kwargs: Response()
    )

    assert github._primary_email("access") == ("primary@example.com", True)


def test_provider_routers_are_reusable_across_api_versions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    health_route = {
        "app_config": "infrastructure.common.apps.CommonConfig",
        "prefix": "/health",
        "router": "infrastructure.common.api.router",
        "tag": "Health",
    }
    monkeypatch.setattr(
        "config.api.load_api_registry",
        lambda: {"v1": {"routes": [health_route]}, "v2": {"routes": [health_route]}},
    )

    apis = build_apis()

    for version, api in apis.items():
        paths = api.get_openapi_schema(path_prefix=f"/api/{version}")["paths"]
        assert f"/api/{version}/oauth/google/start" in paths
        assert f"/api/{version}/oauth/apple/callback" in paths
