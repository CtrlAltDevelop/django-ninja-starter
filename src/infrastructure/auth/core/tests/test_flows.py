"""The shared login-completion path and the helpers each method leans on."""

from typing import Any
from unittest.mock import patch

import pytest
from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser
from django.contrib.sessions.backends.db import SessionStore
from django.test import Client, RequestFactory, override_settings

from infrastructure.auth.core.challenges import get_challenge_store
from infrastructure.auth.core.errors import AuthError
from infrastructure.auth.core.flows import (
    LoginResult,
    complete_login,
    consume_codeless,
    decoy_challenge,
    enrolled_second_factors,
    pending_methods,
    resolve_pending_login,
    user_from_challenge,
)
from infrastructure.auth.core.models import AuthEvent, AuthEventType
from infrastructure.auth.core.schemas import login_out, mask, mask_email, mask_phone
from infrastructure.auth.core.sessions import IssuedCredentials
from infrastructure.auth.twofactor.models import SecondFactor, SecondFactorMethod


@pytest.fixture
def user(db: None) -> Any:
    return get_user_model()._default_manager.create_user(username="zoe")


def _request() -> Any:
    request = RequestFactory().post("/api/v1/auth/password/login")
    request.session = SessionStore()
    request.user = AnonymousUser()
    return request


def test_a_result_with_credentials_is_finished() -> None:
    result = LoginResult(credentials=IssuedCredentials(token_type="bearer"))

    assert result.requires_second_factor is False


def test_a_result_without_credentials_still_owes_a_factor() -> None:
    assert LoginResult(pending_ticket="t", methods=["totp"]).requires_second_factor is True


def test_an_account_with_no_factor_is_signed_in_immediately(user: Any) -> None:
    result = complete_login(_request(), user, method="password", identifier="zoe")

    assert result.requires_second_factor is False
    assert result.credentials is not None
    assert AuthEvent.objects.filter(event_type=AuthEventType.LOGIN_SUCCEEDED).exists()


def test_a_disabled_account_is_refused_before_any_token_is_minted(user: Any) -> None:
    user.is_active = False
    user.save(update_fields=["is_active"])

    with pytest.raises(AuthError) as caught:
        complete_login(_request(), user, method="password")

    assert caught.value.status == 403


def test_an_enrolled_factor_parks_the_login(user: Any) -> None:
    SecondFactor.objects.create(user=user, method=SecondFactorMethod.TOTP).confirm()

    result = complete_login(_request(), user, method="password")

    assert result.requires_second_factor is True
    assert result.methods == ["totp"]
    assert AuthEvent.objects.filter(event_type=AuthEventType.SECOND_FACTOR_REQUIRED).exists()


def test_an_unconfirmed_factor_does_not_park_the_login(user: Any) -> None:
    SecondFactor.objects.create(user=user, method=SecondFactorMethod.TOTP)

    assert complete_login(_request(), user, method="password").requires_second_factor is False


def test_factors_are_ignored_when_the_two_factor_app_is_absent(user: Any) -> None:
    """A project can enable a login method without enabling 2FA at all."""
    SecondFactor.objects.create(user=user, method=SecondFactorMethod.TOTP).confirm()

    with patch("infrastructure.auth.core.flows.app_installed", return_value=False):
        assert enrolled_second_factors(user) == []


def test_a_pending_ticket_recovers_its_account(user: Any) -> None:
    SecondFactor.objects.create(user=user, method=SecondFactorMethod.TOTP).confirm()
    result = complete_login(_request(), user, method="password")

    recovered, metadata = resolve_pending_login(result.pending_ticket)

    assert recovered == user
    assert pending_methods(metadata) == ["totp"]
    assert metadata["first_factor"] == "password"


def test_a_pending_ticket_for_a_deleted_account_is_void(user: Any) -> None:
    SecondFactor.objects.create(user=user, method=SecondFactorMethod.TOTP).confirm()
    ticket = complete_login(_request(), user, method="password").pending_ticket
    user.delete()

    with pytest.raises(AuthError, match="no longer valid"):
        resolve_pending_login(ticket)


def test_pending_methods_copes_with_empty_metadata() -> None:
    assert pending_methods({}) == []


def test_a_codeless_ticket_is_spent_when_read(db: None) -> None:
    ticket = get_challenge_store().create(purpose="link", subject="7")

    assert consume_codeless(ticket, "link").subject == "7"

    from infrastructure.auth.core.challenges import ChallengeExpired

    with pytest.raises(ChallengeExpired):
        consume_codeless(ticket, "link")


def test_a_decoy_ticket_belongs_to_nobody(db: None) -> None:
    """It must look real to the caller and resolve to no account."""
    ticket = decoy_challenge("password_reset", channel="email", destination="ghost@example.com")
    challenge = get_challenge_store().read(ticket, purpose="password_reset")

    assert challenge.destination == "ghost@example.com"
    assert challenge.subject == ""
    with pytest.raises(AuthError):
        user_from_challenge(challenge)


def test_a_challenge_naming_a_missing_account_is_refused(db: None) -> None:
    ticket = get_challenge_store().create(purpose="x", subject="999999", code="123456")
    challenge = get_challenge_store().verify(ticket, "123456", purpose="x")

    with pytest.raises(AuthError, match="not valid"):
        user_from_challenge(challenge)


def test_login_out_renders_the_finished_branch() -> None:
    body = login_out(
        LoginResult(credentials=IssuedCredentials(token_type="bearer", access_token="a"))
    )

    assert body.requires_second_factor is False
    assert body.credentials is not None
    assert body.credentials.access_token == "a"
    assert body.login_ticket == ""


def test_login_out_renders_the_pending_branch() -> None:
    body = login_out(LoginResult(pending_ticket="t", methods=["sms"]))

    assert body.requires_second_factor is True
    assert body.credentials is None
    assert body.login_ticket == "t"
    assert body.methods == ["sms"]


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("zoe@example.com", "z***@example.com"),
        ("a@b.test", "a***@b.test"),
        ("not-an-address", "***"),
    ],
)
def test_email_masking_keeps_the_domain_and_one_letter(value: str, expected: str) -> None:
    assert mask_email(value) == expected


@pytest.mark.parametrize(
    ("value", "expected"), [("+14155550101", "***0101"), ("+123", "***"), ("", "***")]
)
def test_phone_masking_keeps_only_the_last_four(value: str, expected: str) -> None:
    assert mask_phone(value) == expected


def test_an_unknown_channel_masks_everything() -> None:
    assert mask("carrier-pigeon", "somewhere") == "***"


@override_settings(AUTH_TOKEN_MODE="none")
def test_the_none_mode_reports_a_session_credential(user: Any) -> None:
    result = complete_login(_request(), user, method="password")

    assert result.credentials is not None
    assert result.credentials.token_type == "session"


def test_an_identity_error_becomes_a_400(db: None) -> None:
    """Registered on the API, so a malformed identifier is a client error."""
    response = Client().post(
        "/api/v1/auth/email-code/login/start",
        {"email": "not-an-address"},
        content_type="application/json",
    )

    assert response.status_code == 400
    assert "valid email" in response.json()["description"]
