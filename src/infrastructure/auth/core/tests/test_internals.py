"""Edge paths the happy-route tests never reach."""

from typing import Any
from unittest.mock import patch

import fakeredis
import pytest
from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser
from django.contrib.sessions.backends.db import SessionStore
from django.test import Client, RequestFactory, override_settings

from infrastructure.auth.core import challenges as challenge_module
from infrastructure.auth.core.challenges import (
    RedisChallengeStore,
    get_challenge_store,
    reset_challenge_store,
)
from infrastructure.auth.core.identities import IdentityError, _unique_username
from infrastructure.auth.core.models import AuthEvent, AuthEventType, PhoneNumber
from infrastructure.auth.core.sessions import api_auth, revoke_all_for_user
from infrastructure.auth.core.throttling import RateLimited, fingerprint, guard_attempts


@pytest.fixture
def user(db: None) -> Any:
    return get_user_model()._default_manager.create_user(username="zoe")


def _request(**headers: str) -> Any:
    request = RequestFactory().post("/api/v1/auth/password/login", **headers)
    request.session = SessionStore()
    request.user = AnonymousUser()
    return request


def test_the_redis_store_builds_its_client_from_the_configured_url() -> None:
    """The lazy client is what production uses; nothing else exercises it."""
    store = RedisChallengeStore()

    with patch("redis.Redis.from_url", return_value=fakeredis.FakeRedis()) as from_url:
        ticket = store.create(purpose="login", subject="7", code="123456")

        from_url.assert_called_once()
    assert store.verify(ticket, "123456", purpose="login").subject == "7"


def test_the_store_is_rebuilt_when_its_setting_changes() -> None:
    reset_challenge_store()
    first = get_challenge_store()

    with override_settings(
        AUTH_CHALLENGE_STORE="infrastructure.auth.core.challenges.LocMemChallengeStore"
    ):
        assert get_challenge_store() is not first


def test_an_unimportable_store_is_a_configuration_error() -> None:
    from django.core.exceptions import ImproperlyConfigured

    reset_challenge_store()
    with (
        override_settings(AUTH_CHALLENGE_STORE="nowhere.NoSuchStore"),
        pytest.raises(ImproperlyConfigured, match="not importable"),
    ):
        get_challenge_store()


def test_the_store_module_exposes_its_key_prefixes() -> None:
    assert challenge_module.KEY_PREFIX.startswith("auth:")
    assert challenge_module.COUNTER_PREFIX.startswith("auth:")


def test_records_describe_themselves(user: Any) -> None:
    phone = PhoneNumber.objects.create(user=user, number="+14155550101")
    event = AuthEvent.objects.create(event_type=AuthEventType.LOGIN_SUCCEEDED)

    assert str(phone) == "+14155550101"
    assert str(event) == f"login_succeeded:{event.id}"


def test_second_factor_records_describe_themselves(user: Any) -> None:
    from infrastructure.auth.twofactor.models import (
        RecoveryCode,
        SecondFactor,
        SecondFactorMethod,
    )

    factor = SecondFactor.objects.create(user=user, method=SecondFactorMethod.TOTP)
    code = RecoveryCode.objects.create(user=user, code_hash="a" * 64)

    assert str(factor) == f"{user.pk}:totp"
    assert str(code) == f"{user.pk}:{code.id}"


def test_a_username_that_cannot_be_allocated_is_an_error(db: None) -> None:
    """Every candidate collides, so the caller is told rather than looping forever."""
    with (
        patch("infrastructure.auth.core.identities.secrets.token_hex", return_value="deadbeef"),
        patch("infrastructure.accounts.managers.UserManager.filter") as manager_filter,
    ):
        manager_filter.return_value.exists.return_value = True
        with pytest.raises(IdentityError, match="Could not allocate"):
            _unique_username("zoe")


def test_revoking_everything_skips_modes_that_are_not_installed(user: Any) -> None:
    with patch("infrastructure.auth.core.sessions.apps.is_installed", return_value=False):
        assert revoke_all_for_user(user) == 0


def test_api_auth_turns_away_a_request_with_no_credential(db: None) -> None:
    assert api_auth(_request()) is None


def test_api_auth_attaches_the_user_it_resolved(user: Any) -> None:
    from infrastructure.auth.core.sessions import issue_credentials

    credentials = issue_credentials(_request(), user, method="password")
    request = _request(HTTP_AUTHORIZATION=f"Bearer {credentials.access_token}")

    assert api_auth(request) == user
    assert request.user == user


def test_a_fingerprint_hides_the_value_it_counts() -> None:
    digest = fingerprint("zoe@example.com")

    assert "zoe" not in digest
    assert len(digest) == 32
    assert digest == fingerprint("zoe@example.com")


def test_attempt_guards_can_be_switched_off(db: None) -> None:
    for _ in range(50):
        guard_attempts("scope", "subject", limit=0)


def test_attempt_guards_stop_at_their_limit(db: None) -> None:
    guard_attempts("scope", "subject", limit=2)
    guard_attempts("scope", "subject", limit=2)

    with pytest.raises(RateLimited) as caught:
        guard_attempts("scope", "subject", limit=2)

    assert caught.value.retry_after > 0


def test_exhausting_the_code_attempts_answers_429(db: None) -> None:
    """The cap has its own status, so a client can tell it from a wrong code."""
    client = Client()
    start = client.post(
        "/api/v1/auth/email-code/login/start",
        {"email": "zoe@example.com"},
        content_type="application/json",
    )
    ticket = start.json()["data"]["ticket"]

    statuses = [
        client.post(
            "/api/v1/auth/email-code/login/verify",
            {"ticket": ticket, "code": "000000"},
            content_type="application/json",
        ).status_code
        for _ in range(7)
    ]

    assert 400 in statuses
    assert 429 in statuses


@override_settings(AUTH_TOKEN_MODE="none")
def test_the_none_mode_identifies_its_caller_from_the_session(user: Any) -> None:
    from infrastructure.auth.core.sessions import issue_credentials, resolve_request_user

    request = _request()
    issue_credentials(request, user, method="password")
    request.user = user

    assert resolve_request_user(request) == user


@override_settings(AUTH_TOKEN_MODE="none")
def test_the_none_mode_turns_away_an_anonymous_caller(db: None) -> None:
    from infrastructure.auth.core.sessions import resolve_request_user

    assert resolve_request_user(_request()) is None


def test_corrupt_metadata_degrades_to_an_empty_mapping(db: None) -> None:
    """A hand-edited or truncated record must not take the whole flow down."""
    from infrastructure.auth.core.challenges import LocMemChallengeStore, ticket_key

    store = LocMemChallengeStore()
    ticket = store.create(purpose="pending", subject="7", metadata={"methods": ["totp"]})
    store._records[ticket_key(ticket)][1]["metadata"] = "{not json"

    assert store.read(ticket, purpose="pending").metadata == {}
