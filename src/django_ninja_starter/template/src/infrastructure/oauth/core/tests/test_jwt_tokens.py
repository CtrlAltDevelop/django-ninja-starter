"""What the signed-credential layer accepts, and what it must refuse."""

from datetime import timedelta

import jwt
import pytest
from django.core.exceptions import ImproperlyConfigured
from django.test import override_settings

from infrastructure.oauth.core import jwt_tokens

LIFETIME = timedelta(minutes=5)


def _mint(**overrides: object) -> str:
    arguments: dict[str, object] = {
        "subject": "42",
        "handle": jwt_tokens.new_handle(),
        "token_type": jwt_tokens.ACCESS,
        "lifetime": LIFETIME,
        "mode": "rotation",
    }
    arguments.update(overrides)
    token, _ = jwt_tokens.mint(**arguments)  # type: ignore[arg-type]
    return token


def test_a_minted_token_decodes_back_to_what_went_into_it() -> None:
    handle = jwt_tokens.new_handle()
    token = _mint(handle=handle, session_id="session-1", methods=["password", "totp"])

    claims = jwt_tokens.decode(token, token_type=jwt_tokens.ACCESS)

    assert claims.handle == handle
    assert claims.subject == "42"
    assert claims.mode == "rotation"
    assert claims.session_id == "session-1"
    assert claims.methods == ["password", "totp"]


def test_each_handle_is_distinct() -> None:
    assert jwt_tokens.new_handle() != jwt_tokens.new_handle()


def test_a_refresh_token_is_not_accepted_as_an_access_token() -> None:
    """The type claim is the only thing separating the two endpoints."""
    token = _mint(token_type=jwt_tokens.REFRESH)

    with pytest.raises(jwt_tokens.JwtError, match="Expected a access token"):
        jwt_tokens.decode(token, token_type=jwt_tokens.ACCESS)


def test_an_expired_token_is_refused() -> None:
    """Comfortably past expiry, so the configured clock leeway cannot cover it."""
    token = _mint(lifetime=-LIFETIME)

    with pytest.raises(jwt_tokens.JwtError, match="expired"):
        jwt_tokens.decode(token, token_type=jwt_tokens.ACCESS)


def test_an_empty_credential_is_refused_without_parsing() -> None:
    with pytest.raises(jwt_tokens.JwtError, match="No credential"):
        jwt_tokens.decode("", token_type=jwt_tokens.ACCESS)


def test_a_token_signed_with_another_key_is_refused() -> None:
    token = _mint()

    with (
        override_settings(AUTH_JWT_SIGNING_KEY="a-completely-different-signing-key"),
        pytest.raises(jwt_tokens.JwtError, match="not valid"),
    ):
        jwt_tokens.decode(token, token_type=jwt_tokens.ACCESS)


def test_a_token_from_another_issuer_is_refused() -> None:
    with override_settings(AUTH_JWT_ISSUER="somebody-else"):
        token = _mint()

    with pytest.raises(jwt_tokens.JwtError, match="not valid"):
        jwt_tokens.decode(token, token_type=jwt_tokens.ACCESS)


def test_garbage_is_refused_rather_than_raising_something_else() -> None:
    with pytest.raises(jwt_tokens.JwtError, match="not valid"):
        jwt_tokens.decode("not.a.token", token_type=jwt_tokens.ACCESS)


@override_settings(AUTH_JWT_AUDIENCE="billing-api")
def test_an_audience_is_carried_and_enforced_once_configured() -> None:
    token = _mint()

    assert jwt_tokens.decode(token, token_type=jwt_tokens.ACCESS).subject == "42"

    with (
        override_settings(AUTH_JWT_AUDIENCE="some-other-api"),
        pytest.raises(jwt_tokens.JwtError, match="not valid"),
    ):
        jwt_tokens.decode(token, token_type=jwt_tokens.ACCESS)


def test_a_token_minted_without_an_audience_carries_no_aud_claim() -> None:
    """Verification has to be skipped rather than run against an empty string."""
    token = _mint()

    claims = jwt.decode(
        token,
        jwt_tokens.verifying_key(),
        algorithms=["HS256"],
        options={"verify_iss": False},
    )

    assert "aud" not in claims


@override_settings(AUTH_JWT_ALGORITHM="HS512")
def test_another_hmac_algorithm_works_end_to_end() -> None:
    assert jwt_tokens.decode(_mint(), token_type=jwt_tokens.ACCESS).subject == "42"


@override_settings(AUTH_JWT_ALGORITHM="NONE")
def test_an_unsupported_algorithm_is_a_configuration_error() -> None:
    with pytest.raises(ImproperlyConfigured, match="DJANGO_AUTH_JWT_ALGORITHM"):
        _mint()


@override_settings(AUTH_JWT_ALGORITHM="RS256", AUTH_JWT_SIGNING_KEY="")
def test_an_asymmetric_algorithm_refuses_to_derive_a_private_key() -> None:
    """There is nothing to fall back to, so failing loudly is the only safe answer."""
    with pytest.raises(ImproperlyConfigured, match="DJANGO_AUTH_JWT_SIGNING_KEY"):
        jwt_tokens.signing_key()


@override_settings(
    AUTH_JWT_ALGORITHM="RS256",
    AUTH_JWT_SIGNING_KEY="-----BEGIN PRIVATE KEY-----",
    AUTH_JWT_VERIFYING_KEY="",
)
def test_an_asymmetric_algorithm_needs_its_public_key_too() -> None:
    with pytest.raises(ImproperlyConfigured, match="DJANGO_AUTH_JWT_VERIFYING_KEY"):
        jwt_tokens.verifying_key()


def test_hmac_verifies_with_the_very_key_it_signed_with() -> None:
    assert jwt_tokens.verifying_key() == jwt_tokens.signing_key()
