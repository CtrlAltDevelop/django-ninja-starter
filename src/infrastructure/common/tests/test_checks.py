"""The declared-settings check, and the contracts the installed apps declare.

Two jobs here. The first half tests the machinery against made-up requirements,
so a rule can be exercised without bending the project's real configuration. The
second half tests the declarations themselves -- that every path resolves, that
the suite's own settings satisfy them, and that turning an app off silences it.
"""

from typing import Any

import pytest
from django.apps import apps
from django.conf import settings
from django.core.checks import Error, Warning
from django.test import override_settings

from infrastructure.common.appsettings import MISSING, AppSettings, Requirement, Rule
from infrastructure.common.checks import (
    _check,
    _check_rule,
    check_declared_app_settings,
    installed_specs,
)

SPEC = AppSettings(title="Thing")


def _messages(requirement: Requirement) -> list[Error | Warning]:
    return list(_check("thing", SPEC, requirement))


def _ids(requirement: Requirement) -> set[str]:
    return {message.id for message in _messages(requirement)}


def test_a_path_that_names_no_setting_at_all_is_an_error() -> None:
    messages = _messages(Requirement("NOT_A_REAL_SETTING", purpose="nothing"))

    assert [message.id for message in messages] == ["thing.NOT_A_REAL_SETTING"]
    assert "not defined in settings" in messages[0].msg


@override_settings(THING="")
def test_a_required_setting_left_empty_is_an_error() -> None:
    messages = _messages(
        Requirement("THING", purpose="the thing itself", env="DJANGO_THING", required=True)
    )

    assert isinstance(messages[0], Error)
    assert "the thing itself" in messages[0].msg
    assert messages[0].hint == "Set DJANGO_THING."


@override_settings(THING="")
def test_a_recommended_setting_left_empty_is_only_a_warning() -> None:
    messages = _messages(Requirement("THING", purpose="a nicety", recommended=True))

    assert isinstance(messages[0], Warning)


@override_settings(THING="")
def test_an_optional_setting_left_empty_says_nothing() -> None:
    assert _messages(Requirement("THING", purpose="whatever")) == []


@pytest.mark.parametrize("empty", ["", None, [], {}])
def test_every_shape_of_empty_counts_as_missing(empty: Any) -> None:
    with override_settings(THING=empty):
        assert _ids(Requirement("THING", purpose="p", required=True)) == {"thing.THING"}


@override_settings(THING="filled")
def test_a_filled_required_setting_says_nothing() -> None:
    assert _messages(Requirement("THING", purpose="p", required=True)) == []


@pytest.mark.parametrize("value", [5, 5.0, 200])
def test_a_number_outside_its_range_is_an_error(value: float) -> None:
    with override_settings(THING=value):
        assert _ids(Requirement("THING", purpose="p", minimum=10, maximum=100)) == {"thing.THING"}


@override_settings(THING=50)
def test_a_number_inside_its_range_says_nothing() -> None:
    assert _messages(Requirement("THING", purpose="p", minimum=10, maximum=100)) == []


@override_settings(THING=True)
def test_a_boolean_is_not_range_checked_as_a_number() -> None:
    """True would otherwise read as 1 and trip a minimum it was never meant to meet."""
    assert _messages(Requirement("THING", purpose="p", minimum=10, maximum=100)) == []


@override_settings(THING="mauve")
def test_a_value_outside_its_choices_is_an_error() -> None:
    messages = _messages(Requirement("THING", purpose="p", choices=("red", "blue")))

    assert isinstance(messages[0], Error)
    assert "red, blue" in messages[0].msg


@override_settings(THING="red")
def test_a_listed_choice_says_nothing() -> None:
    assert _messages(Requirement("THING", purpose="p", choices=("red", "blue"))) == []


@override_settings(THING="console-backend")
def test_a_development_default_left_in_place_warns() -> None:
    messages = _messages(
        Requirement(
            "THING", purpose="p", unsafe_defaults=("console-backend",), hint="Use a real one."
        )
    )

    assert isinstance(messages[0], Warning)
    assert "development default" in messages[0].msg
    assert messages[0].hint == "Use a real one."


@override_settings(THING={"provider": {"secret": ""}})
def test_a_nested_path_reaches_into_a_settings_dictionary() -> None:
    """This is how a provider's own credential is addressed."""
    assert _ids(Requirement("THING.provider.secret", purpose="the secret", required=True)) == {
        "thing.THING.provider.secret"
    }


@override_settings(THING={"provider": {"secret": "filled"}})
def test_a_nested_path_that_is_filled_says_nothing() -> None:
    assert _messages(Requirement("THING.provider.secret", purpose="p", required=True)) == []


@override_settings(THING={"provider": {}})
def test_a_nested_path_that_does_not_exist_is_an_error() -> None:
    assert Requirement("THING.provider.secret", purpose="p").resolve() is MISSING


@override_settings(THING="not-a-dict")
def test_indexing_into_a_non_dictionary_is_reported_not_raised() -> None:
    assert Requirement("THING.provider", purpose="p").resolve() is MISSING


# --- the real declarations -------------------------------------------------


def test_the_projects_own_configuration_raises_no_errors() -> None:
    """The suite enables every app, so this covers all of them at once."""
    errors = [message.id for message in check_declared_app_settings() if isinstance(message, Error)]

    assert errors == []


def test_the_only_things_the_suite_is_warned_about_are_its_in_process_doubles() -> None:
    """Which is the point of unsafe_defaults: both are fine here and wrong in production.

    A suite cannot run against a real Redis, so it keeps the challenge store and
    the two in-process brokers -- and each of them says so about itself rather
    than being quietly exempted.
    """
    warnings = {
        message.id for message in check_declared_app_settings() if isinstance(message, Warning)
    }

    assert warnings == {
        "auth_core.AUTH_CHALLENGE_STORE",
        "notifications.NOTIFICATIONS_BROKER",
        "support.SUPPORT_BROKER",
    }


def test_the_apps_this_suite_enables_all_declare_a_contract() -> None:
    labels = {label for label, _ in installed_specs()}

    assert labels >= {
        "auth_core",
        "auth_password",
        "auth_email_code",
        "auth_sms_code",
        "auth_magic_link",
        "auth_twofactor",
        "oauth_core",
        "oauth_google",
        "oauth_apple",
        "oauth_microsoft",
        "oauth_github",
        "oauth_sliding",
        "oauth_session",
        "oauth_rotation",
        "notifications",
        "support",
        "cms",
        "shop",
    }


def test_every_declared_path_resolves_to_a_real_setting() -> None:
    """A typo in a path would otherwise be a permanent, unexplained error."""
    unresolved = [
        f"{label}.{requirement.setting}"
        for label, spec in installed_specs()
        for requirement in spec.requirements
        if requirement.resolve() is MISSING
    ]

    assert unresolved == []


def test_every_requirement_names_the_environment_variable_that_fills_it() -> None:
    missing = [
        f"{label}.{requirement.setting}"
        for label, spec in installed_specs()
        for requirement in spec.requirements
        if not requirement.env
    ]

    assert missing == []


def test_a_check_id_is_scoped_to_the_app_that_declared_it() -> None:
    """So one nag can be silenced without silencing a whole category."""
    ids = {
        message.id
        for message in _check(
            "auth_magic_link",
            AppSettings(title="Magic-link login"),
            Requirement("AUTH_MAGIC_LINK_BASE_URL", purpose="p", required=True),
        )
    }
    with override_settings(AUTH_MAGIC_LINK_BASE_URL=""):
        assert (
            _check(
                "auth_magic_link",
                AppSettings(title="Magic-link login"),
                Requirement("AUTH_MAGIC_LINK_BASE_URL", purpose="p", required=True),
            )[0].id
            == "auth_magic_link.AUTH_MAGIC_LINK_BASE_URL"
        )
    assert ids == set()


@pytest.mark.parametrize(
    ("label", "setting"),
    [
        ("auth_magic_link", "AUTH_MAGIC_LINK_BASE_URL"),
        ("auth_sms_code", "AUTH_SMS_BACKEND"),
        ("oauth_google", "OAUTH_PROVIDER_CONFIG.google.client_secret"),
        ("oauth_rotation", "AUTH_REFRESH_TOKEN_TTL_SECONDS"),
    ],
)
def test_each_app_declares_the_setting_it_cannot_work_without(label: str, setting: str) -> None:
    spec = dict(installed_specs())[label]

    assert setting in {requirement.setting for requirement in spec.requirements}


def test_an_uninstalled_app_contributes_no_requirements() -> None:
    """Enabling an app is what activates its contract, which is the point.

    Nothing here is installed under a label that does not exist, so the registry
    walk simply never reaches it -- a deployment is told what the apps it turned
    on still need, and nothing about the rest.
    """
    labels = {label for label, _ in installed_specs()}

    assert "auth_webauthn" not in labels
    assert all(apps.get_app_config(label) is not None for label in labels)


def test_the_summary_and_title_are_filled_in_for_every_app() -> None:
    """They are what the generated documentation and the messages read from."""
    for label, spec in installed_specs():
        assert spec.title, label
        assert spec.summary, label


def test_a_setting_the_suite_blanks_out_is_reported_against_its_own_app() -> None:
    with override_settings(AUTH_MAGIC_LINK_BASE_URL=""):
        errors = {
            message.id for message in check_declared_app_settings() if isinstance(message, Error)
        }

    assert errors == {"auth_magic_link.AUTH_MAGIC_LINK_BASE_URL"}


def test_a_provider_credential_the_suite_blanks_out_is_reported() -> None:
    config = {**settings.OAUTH_PROVIDER_CONFIG}
    config["github"] = {**config["github"], "client_secret": ""}

    with override_settings(OAUTH_PROVIDER_CONFIG=config):
        errors = {
            message.id for message in check_declared_app_settings() if isinstance(message, Error)
        }

    assert errors == {"oauth_github.OAUTH_PROVIDER_CONFIG.github.client_secret"}


# -- the shape of a value, and relationships between several -------------------


def test_a_value_of_the_wrong_shape_is_an_error() -> None:
    with override_settings(SHOP_CURRENCY="dollars"):
        messages = _messages(
            Requirement(
                "SHOP_CURRENCY",
                purpose="the currency",
                pattern=r"[A-Z]{3}",
                pattern_description="be a three-letter ISO 4217 code, such as USD",
            )
        )

    assert [message.id for message in messages] == ["thing.SHOP_CURRENCY"]
    assert "three-letter ISO 4217 code" in messages[0].msg


def test_a_pattern_has_to_match_the_whole_value() -> None:
    """`USDollar` starts with three capitals, and is not a currency code."""
    with override_settings(SHOP_CURRENCY="USDollar"):
        assert _ids(Requirement("SHOP_CURRENCY", purpose="p", pattern=r"[A-Z]{3}")) == {
            "thing.SHOP_CURRENCY"
        }


def test_a_value_of_the_right_shape_passes() -> None:
    with override_settings(SHOP_CURRENCY="EUR"):
        assert _ids(Requirement("SHOP_CURRENCY", purpose="p", pattern=r"[A-Z]{3}")) == set()


def test_a_requirement_its_condition_rules_out_is_not_checked_at_all() -> None:
    """A deployment nagged about a setting its own configuration made irrelevant
    learns to ignore the checker."""
    requirement = Requirement(
        "NOT_A_REAL_SETTING",
        purpose="nothing",
        required=True,
        applies_when=lambda: False,
    )

    assert _messages(requirement) == []


def test_a_requirement_its_condition_admits_is_checked_as_usual() -> None:
    requirement = Requirement(
        "NOT_A_REAL_SETTING",
        purpose="nothing",
        required=True,
        applies_when=lambda: True,
    )

    assert _ids(requirement) == {"thing.NOT_A_REAL_SETTING"}


def test_a_rule_that_holds_reports_nothing() -> None:
    assert _check_rule("thing", SPEC, Rule(holds=lambda: True, message="never said")) == []


def test_a_rule_that_fails_is_an_error_against_its_first_setting() -> None:
    messages = _check_rule(
        "shop",
        AppSettings(title="Shop"),
        Rule(
            holds=lambda: False,
            message="the ceiling is below the default",
            settings=("SHOP_MAX_PAGE_SIZE", "SHOP_PAGE_SIZE"),
        ),
    )

    assert [message.id for message in messages] == ["shop.SHOP_MAX_PAGE_SIZE"]
    assert "the ceiling is below the default" in messages[0].msg


# -- every feature app, not only the ones that had a contract first ------------


@pytest.mark.parametrize("label", ["cms", "notifications", "shop", "support"])
def test_a_feature_app_declares_the_flag_that_installs_it(label: str) -> None:
    """Installing an app without turning it on migrates its tables and publishes
    none of its routes, which is worth being told rather than discovering."""
    spec = dict(installed_specs())[label]

    assert f"{label.upper()}_ENABLED" in {requirement.setting for requirement in spec.requirements}


@pytest.mark.parametrize(
    ("label", "setting"),
    [
        ("cms", "CMS_UPLOAD_PATH"),
        ("cms", "CMS_MAX_UPLOAD_MB"),
        ("notifications", "NOTIFICATIONS_WS_PATH"),
        ("notifications", "NOTIFICATIONS_CHANNEL_PREFIX"),
        ("shop", "SHOP_CURRENCY"),
        ("shop", "SHOP_MAX_PAGE_SIZE"),
        ("support", "SUPPORT_WS_PATH"),
        ("support", "SUPPORT_REFERENCE_PREFIX"),
        ("support", "SUPPORT_UPLOAD_PATH"),
    ],
)
def test_each_feature_app_declares_the_settings_it_reads(label: str, setting: str) -> None:
    spec = dict(installed_specs())[label]

    assert setting in {requirement.setting for requirement in spec.requirements}


def test_every_setting_a_feature_app_declares_is_one_the_project_defines() -> None:
    """The same guarantee the infrastructure apps get: a typo in a path would
    otherwise be a permanent error nobody can clear."""
    for label in ("cms", "notifications", "shop", "support"):
        spec = dict(installed_specs())[label]
        for requirement in spec.requirements:
            assert requirement.resolve() is not MISSING, f"{label}.{requirement.setting}"


def test_an_app_installed_but_not_enabled_is_an_error() -> None:
    """`DJANGO_SHOP_ENABLED` is what installs the shop, so the two cannot
    disagree unless somebody edited INSTALLED_APPS by hand -- and then the tables
    migrate and no route is published."""
    with override_settings(SHOP_ENABLED=False):
        errors = {
            message.id for message in check_declared_app_settings() if isinstance(message, Error)
        }

    assert "shop.SHOP_ENABLED" in errors


def test_a_currency_that_is_not_a_code_stops_the_shop() -> None:
    with override_settings(SHOP_CURRENCY="dollars"):
        errors = {
            message.id for message in check_declared_app_settings() if isinstance(message, Error)
        }

    assert "shop.SHOP_CURRENCY" in errors


def test_a_page_ceiling_below_the_default_page_size_is_an_error() -> None:
    """Two individually reasonable numbers in the wrong order: no requirement on
    its own can see it."""
    with override_settings(SHOP_PAGE_SIZE=50, SHOP_MAX_PAGE_SIZE=10):
        errors = {
            message.id for message in check_declared_app_settings() if isinstance(message, Error)
        }

    assert "shop.SHOP_MAX_PAGE_SIZE" in errors


def test_the_redis_url_is_only_required_once_the_broker_is_the_redis_one() -> None:
    memory = "apps.support.broadcast.MemoryBroker"
    redis = "apps.support.broadcast.RedisBroker"

    with override_settings(SUPPORT_BROKER=memory, SUPPORT_REDIS_URL=""):
        quiet = {message.id for message in check_declared_app_settings()}
    with override_settings(SUPPORT_BROKER=redis, SUPPORT_REDIS_URL=""):
        loud = {message.id for message in check_declared_app_settings()}

    assert "support.SUPPORT_REDIS_URL" not in quiet
    assert "support.SUPPORT_REDIS_URL" in loud
