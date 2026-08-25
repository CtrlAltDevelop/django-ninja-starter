"""The system checks that catch an unworkable authentication configuration."""

from unittest.mock import patch

import pytest
from django.test import override_settings

from infrastructure.auth.core.checks import check_auth_settings

CONSOLE_SMS = "infrastructure.auth.core.delivery.ConsoleSmsBackend"
LOCMEM_STORE = "infrastructure.auth.core.challenges.LocMemChallengeStore"
REDIS_STORE = "infrastructure.auth.core.challenges.RedisChallengeStore"


def _ids(**overrides: object) -> set[str]:
    defaults = {
        "AUTH_CHALLENGE_STORE": REDIS_STORE,
        "AUTH_SMS_BACKEND": "project.sms.TwilioBackend",
        "AUTH_METHODS": [],
        "AUTH_SECOND_FACTORS": [],
        "AUTH_MAGIC_LINK_BASE_URL": "",
        "AUTH_PASSWORD_RESET_BASE_URL": "",
    }
    with override_settings(**{**defaults, **overrides}):
        return {message.id for message in check_auth_settings()}


def test_a_sound_configuration_raises_nothing() -> None:
    assert _ids() == set()


def test_a_token_mode_without_its_app_is_an_error() -> None:
    with patch("infrastructure.auth.core.checks.apps.is_installed", return_value=False):
        assert "auth.E001" in _ids(AUTH_TOKEN_MODE="rotation")


def test_the_none_token_mode_needs_no_app() -> None:
    with patch("infrastructure.auth.core.checks.apps.is_installed", return_value=False):
        assert "auth.E001" not in _ids(AUTH_TOKEN_MODE="none")


@pytest.mark.parametrize(
    ("setting", "value", "expected"),
    [
        ("AUTH_CHALLENGE_TTL_SECONDS", 5, "auth.E002"),
        ("AUTH_CHALLENGE_TTL_SECONDS", 99999, "auth.E002"),
        ("AUTH_CODE_DIGITS", 2, "auth.E003"),
        ("AUTH_CODE_DIGITS", 99, "auth.E003"),
        ("AUTH_CHALLENGE_MAX_ATTEMPTS", 0, "auth.E004"),
        ("AUTH_CHALLENGE_MAX_ATTEMPTS", 500, "auth.E004"),
        ("AUTH_RECOVERY_CODE_COUNT", 1, "auth.E005"),
        ("AUTH_RECOVERY_CODE_COUNT", 500, "auth.E005"),
    ],
)
def test_values_outside_their_workable_range_are_errors(
    setting: str, value: int, expected: str
) -> None:
    assert expected in _ids(**{setting: value})


def test_magic_link_without_a_landing_page_is_an_error() -> None:
    """The emailed link would otherwise point nowhere."""
    assert "auth.E006" in _ids(AUTH_METHODS=["magic_link"], AUTH_MAGIC_LINK_BASE_URL="")
    assert "auth.E006" not in _ids(
        AUTH_METHODS=["magic_link"], AUTH_MAGIC_LINK_BASE_URL="https://example.test/link"
    )


def test_the_in_memory_store_warns_that_it_will_not_scale() -> None:
    assert "auth.W001" in _ids(AUTH_CHALLENGE_STORE=LOCMEM_STORE)


def test_logging_sms_codes_instead_of_sending_them_warns() -> None:
    assert "auth.W002" in _ids(AUTH_METHODS=["sms_code"], AUTH_SMS_BACKEND=CONSOLE_SMS)
    assert "auth.W002" in _ids(AUTH_SECOND_FACTORS=["sms"], AUTH_SMS_BACKEND=CONSOLE_SMS)
    assert "auth.W002" not in _ids(AUTH_METHODS=["password"], AUTH_SMS_BACKEND=CONSOLE_SMS)


def test_password_reset_without_a_landing_page_only_warns() -> None:
    """Reset still works from the returned ticket, so this is not fatal."""
    assert "auth.W003" in _ids(AUTH_METHODS=["password"], AUTH_PASSWORD_RESET_BASE_URL="")
    assert "auth.W003" not in _ids(
        AUTH_METHODS=["password"], AUTH_PASSWORD_RESET_BASE_URL="https://example.test/reset"
    )
