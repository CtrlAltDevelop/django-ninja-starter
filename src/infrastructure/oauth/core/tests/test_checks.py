"""The OAuth checks that a per-setting declaration cannot express.

Which credentials each provider needs is declared on its app config; these are
the format rules and the one question that spans two settings.
"""

from django.test import override_settings

from infrastructure.oauth.core.checks import check_social_oauth_settings


def _ids(**overrides: object) -> set[str]:
    with override_settings(**overrides):
        return {message.id for message in check_social_oauth_settings()}


def test_the_suites_own_configuration_raises_nothing() -> None:
    assert _ids() == set()


@override_settings(
    OAUTH_PROVIDERS=["apple"],
    OAUTH_PROVIDER_CONFIG={"apple": {"redirect_uri": "http://example.test/callback"}},
)
def test_apple_refuses_a_plain_http_callback() -> None:
    """Apple posts the callback cross-site and will not do it over HTTP."""
    assert "oauth.E002" in {message.id for message in check_social_oauth_settings()}


@override_settings(
    OAUTH_PROVIDERS=["apple"],
    OAUTH_PROVIDER_CONFIG={"apple": {"redirect_uri": "https://example.test/callback"}},
)
def test_apple_accepts_an_https_callback() -> None:
    assert "oauth.E002" not in {message.id for message in check_social_oauth_settings()}


@override_settings(
    OAUTH_PROVIDERS=["apple"],
    OAUTH_PROVIDER_CONFIG={"apple": {"redirect_uri": ""}},
)
def test_an_unset_apple_callback_is_left_to_the_declared_requirement() -> None:
    """Emptiness is the declaration's business; only a wrong scheme is this check's."""
    assert "oauth.E002" not in {message.id for message in check_social_oauth_settings()}


@override_settings(
    OAUTH_PROVIDERS=["microsoft"],
    OAUTH_PROVIDER_CONFIG={"microsoft": {"tenant": "example.onmicrosoft.com"}},
)
def test_microsoft_tenant_configuration_is_unambiguous() -> None:
    assert "oauth.E003" in {message.id for message in check_social_oauth_settings()}


@override_settings(
    OAUTH_PROVIDERS=["microsoft"],
    OAUTH_PROVIDER_CONFIG={"microsoft": {"tenant": "organizations"}},
)
def test_a_named_microsoft_audience_is_accepted() -> None:
    assert "oauth.E003" not in {message.id for message in check_social_oauth_settings()}


def test_storing_provider_tokens_without_its_own_key_warns() -> None:
    assert "oauth.W002" in _ids(OAUTH_STORE_PROVIDER_TOKENS=True, OAUTH_ENCRYPTION_KEY="")
    assert "oauth.W002" not in _ids(
        OAUTH_STORE_PROVIDER_TOKENS=True, OAUTH_ENCRYPTION_KEY="a-fernet-key"
    )
    assert "oauth.W002" not in _ids(OAUTH_STORE_PROVIDER_TOKENS=False, OAUTH_ENCRYPTION_KEY="")
