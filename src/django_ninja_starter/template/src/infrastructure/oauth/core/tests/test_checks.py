import pytest
from django.test import override_settings

from infrastructure.oauth.core.checks import check_social_oauth_settings


@override_settings(
    OAUTH_PROVIDERS=["google"],
    OAUTH_PROVIDER_CONFIG={
        "google": {
            "client_id": "",
            "client_secret": "",
            "redirect_uri": "",
        },
        "microsoft": {"tenant": "common"},
    },
)
def test_enabled_provider_requires_credentials_and_warns_about_dynamic_callback() -> None:
    messages = check_social_oauth_settings()

    assert {message.id for message in messages} >= {"oauth.E001", "oauth.W001"}


@pytest.mark.parametrize(
    ("setting", "value", "error_id"),
    [
        ("OAUTH_STATE_TTL_SECONDS", 30, "oauth.E004"),
        ("OAUTH_HTTP_TIMEOUT_SECONDS", 0, "oauth.E005"),
        ("OAUTH_CLOCK_SKEW_SECONDS", 301, "oauth.E006"),
    ],
)
def test_unsafe_oauth_limits_are_rejected(setting: str, value: int, error_id: str) -> None:
    with override_settings(**{setting: value}):
        messages = check_social_oauth_settings()

    assert error_id in {message.id for message in messages}


@override_settings(
    OAUTH_PROVIDERS=["microsoft"],
    OAUTH_PROVIDER_CONFIG={
        "microsoft": {
            "client_id": "client",
            "client_secret": "secret",
            "redirect_uri": "https://example.test/callback",
            "tenant": "example.onmicrosoft.com",
        }
    },
)
def test_microsoft_tenant_configuration_is_unambiguous() -> None:
    messages = check_social_oauth_settings()

    assert "oauth.E003" in {message.id for message in messages}
