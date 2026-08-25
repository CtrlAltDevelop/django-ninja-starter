"""The authentication checks that involve more than one setting at a time.

The per-setting rules live on the app configs and are covered by
``infrastructure/common/tests/test_checks.py``; what is exercised here is the
handful of questions a single declaration cannot answer.
"""

from unittest.mock import patch

import pytest
from django.test import override_settings

from infrastructure.auth.core.checks import check_auth_settings

CONSOLE_SMS = "infrastructure.auth.core.delivery.ConsoleSmsBackend"


def _ids(**overrides: object) -> set[str]:
    defaults = {
        "AUTH_SMS_BACKEND": "project.sms.TwilioBackend",
        "AUTH_METHODS": [],
        "AUTH_SECOND_FACTORS": [],
        "AUTH_JWT_ALGORITHM": "HS256",
        "AUTH_JWT_SIGNING_KEY": "a-signing-key-long-enough-for-hs256",
        "AUTH_JWT_VERIFYING_KEY": "",
        "AUTH_JWT_ISSUER": "checks-under-test",
        "AUTH_JWT_LEEWAY_SECONDS": 30,
    }
    with override_settings(**{**defaults, **overrides}):
        return {message.id for message in check_auth_settings()}


def test_a_sound_configuration_raises_nothing() -> None:
    assert _ids() == set()


def test_a_token_mode_without_its_app_is_an_error() -> None:
    with patch("infrastructure.auth.core.checks.app_installed", return_value=False):
        assert "auth.E001" in _ids(AUTH_TOKEN_MODE="rotation")


def test_the_none_token_mode_needs_no_app() -> None:
    with patch("infrastructure.auth.core.checks.app_installed", return_value=False):
        assert "auth.E001" not in _ids(AUTH_TOKEN_MODE="none")


def test_an_sms_second_factor_delivered_to_the_log_warns() -> None:
    """Anyone who can read logs could otherwise clear anyone's second factor.

    The sms_code *login* app states this requirement itself; only the second
    factor needs asking about here, because whether it is in use depends on
    AUTH_SECOND_FACTORS rather than on which apps are installed.
    """
    assert "auth.W002" in _ids(AUTH_SECOND_FACTORS=["sms"], AUTH_SMS_BACKEND=CONSOLE_SMS)
    assert "auth.W002" not in _ids(AUTH_SECOND_FACTORS=["totp"], AUTH_SMS_BACKEND=CONSOLE_SMS)


@pytest.mark.parametrize(
    ("overrides", "expected"),
    [
        ({"AUTH_JWT_ALGORITHM": "HS128"}, "auth.E007"),
        ({"AUTH_JWT_ALGORITHM": "RS256", "AUTH_JWT_SIGNING_KEY": ""}, "auth.E008"),
        (
            {
                "AUTH_JWT_ALGORITHM": "RS256",
                "AUTH_JWT_SIGNING_KEY": "private",
                "AUTH_JWT_VERIFYING_KEY": "",
            },
            "auth.E008",
        ),
        ({"AUTH_JWT_ISSUER": ""}, "auth.E009"),
        ({"AUTH_JWT_LEEWAY_SECONDS": 9999}, "auth.E010"),
        ({"AUTH_JWT_SIGNING_KEY": ""}, "auth.W004"),
    ],
)
def test_an_unusable_signing_setup_is_reported(overrides: dict[str, object], expected: str) -> None:
    assert expected in _ids(AUTH_TOKEN_MODE="rotation", **overrides)


def test_signing_is_not_checked_when_no_token_is_ever_issued() -> None:
    """The `none` mode signs nothing, so an unusable key pair is not its problem."""
    assert _ids(AUTH_TOKEN_MODE="none", AUTH_JWT_ALGORITHM="HS128") == set()


def test_a_key_pair_satisfies_an_asymmetric_algorithm() -> None:
    assert (
        _ids(
            AUTH_TOKEN_MODE="rotation",
            AUTH_JWT_ALGORITHM="ES256",
            AUTH_JWT_SIGNING_KEY="private",
            AUTH_JWT_VERIFYING_KEY="public",
        )
        == set()
    )
