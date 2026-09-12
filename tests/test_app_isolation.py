"""Every app has to work on its own, not only alongside all the others.

The main suite runs with everything enabled, which is the one configuration
nobody deploys. It would happily pass while a single-method project failed to
boot, or booted and issued a credential its own API would not accept.

Settings are read once at import, so a test process cannot un-enable an app it
has already installed. Each scenario therefore gets a fresh interpreter, driven
by ``isolation_driver.py``. That makes these slower than the rest of the suite
and worth every second: this is the only place the *shipped* defaults are
exercised, rather than the test settings that turn everything on.
"""

import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
DRIVER = ROOT / "tests" / "isolation_driver.py"
LOCMEM_STORE = "infrastructure.auth.core.challenges.LocMemChallengeStore"

BASE_ENV = {
    # Empty on purpose: the settings module would otherwise read this
    # repository's own `.env`, and every scenario below would be configured by
    # whatever the developer running the suite happens to have enabled.
    "DJANGO_ENV_FILE": "",
    "DJANGO_SETTINGS_MODULE": "config.settings.development",
    "DJANGO_SECRET_KEY": "isolation-secret-key-long-enough-for-hs256",
    "DJANGO_AUTH_CHALLENGE_STORE": LOCMEM_STORE,
    "DJANGO_AUTH_SMS_BACKEND": "infrastructure.auth.core.delivery.LocMemSmsBackend",
    "DJANGO_AUTH_EMAIL_BACKEND": "infrastructure.auth.core.delivery.LocMemEmailBackend",
    "DJANGO_AUTH_MAGIC_LINK_BASE_URL": "https://example.test/link",
}
METHODS = ["password", "email_code", "sms_code", "magic_link"]
#: The optional apps, each of which has to install, work and be removable on its
#: own. Adding an app here is what gives it isolation coverage.
FEATURE_APPS = ["cms", "notifications", "shop", "support"]
TOKEN_MODES = ["sliding", "session", "rotation"]
PROVIDERS = {
    "google": {
        "GOOGLE_OAUTH_CLIENT_ID": "id",
        "GOOGLE_OAUTH_CLIENT_SECRET": "secret",
        "GOOGLE_OAUTH_REDIRECT_URI": "https://example.test/callback",
    },
    "apple": {
        "APPLE_OAUTH_CLIENT_ID": "com.example.service",
        "APPLE_OAUTH_TEAM_ID": "TEAM",
        "APPLE_OAUTH_KEY_ID": "KEY",
        "APPLE_OAUTH_PRIVATE_KEY": "-----BEGIN PRIVATE KEY-----",
        "APPLE_OAUTH_REDIRECT_URI": "https://example.test/callback",
    },
    "microsoft": {
        "MICROSOFT_OAUTH_CLIENT_ID": "id",
        "MICROSOFT_OAUTH_CLIENT_SECRET": "secret",
        "MICROSOFT_OAUTH_REDIRECT_URI": "https://example.test/callback",
    },
    "github": {
        "GITHUB_OAUTH_CLIENT_ID": "id",
        "GITHUB_OAUTH_CLIENT_SECRET": "secret",
        "GITHUB_OAUTH_REDIRECT_URI": "https://example.test/callback",
    },
}


def _run(
    arguments: list[str], environment: dict[str, str], database: Path
) -> subprocess.CompletedProcess[str]:
    """Run a management command or the driver in a clean environment.

    The environment is built from nothing rather than inherited: the suite's own
    settings module configures itself through ``os.environ.setdefault``, and a
    child that inherited it would be testing this repository's test setup instead
    of the shipped defaults.
    """
    return subprocess.run(
        [sys.executable, *arguments],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
        env={
            "PATH": os.environ.get("PATH", ""),
            "HOME": os.environ.get("HOME", ""),
            **BASE_ENV,
            **environment,
            "DJANGO_DB_NAME": str(database),
        },
    )


def _enabled(app: str) -> str:
    """The environment variable that turns one feature app on."""
    return f"DJANGO_{app.upper()}_ENABLED"


def _check(environment: dict[str, str], database: Path) -> subprocess.CompletedProcess[str]:
    return _run(["manage.py", "check"], environment, database)


def _drive(scenario: str, environment: dict[str, str], database: Path) -> None:
    result = _run([str(DRIVER), scenario], environment, database)

    assert result.returncode == 0, result.stdout[-2000:] + result.stderr[-2000:]
    assert result.stdout.strip().endswith("ok")


@pytest.mark.parametrize("method", METHODS)
def test_a_single_login_method_issues_a_usable_bearer_token(method: str, tmp_path: Path) -> None:
    """Enable one method and nothing else: it must sign somebody in, end to end,
    and the credential has to be one an API client can carry.

    Enabling a login method used to leave the token mode at `none`, which handed
    back a session cookie and an empty access_token -- a working login and an
    unusable API. The driver refuses anything that does not authenticate.
    """
    _drive(method, {"DJANGO_AUTH_METHODS": method}, tmp_path / "db.sqlite3")


@pytest.mark.parametrize("mode", TOKEN_MODES)
def test_one_login_method_works_against_each_token_mode(mode: str, tmp_path: Path) -> None:
    _drive(
        "password",
        {"DJANGO_AUTH_METHODS": "password", "DJANGO_AUTH_TOKEN_MODE": mode},
        tmp_path / "db.sqlite3",
    )


@pytest.mark.parametrize("factor", ["totp", "sms", "email", "recovery"])
def test_one_second_factor_installs_on_its_own(factor: str, tmp_path: Path) -> None:
    """A project should be able to offer exactly one second factor."""
    result = _check(
        {"DJANGO_AUTH_METHODS": "password", "DJANGO_AUTH_SECOND_FACTORS": factor},
        tmp_path / "db.sqlite3",
    )

    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize("provider", sorted(PROVIDERS))
def test_one_oauth_provider_installs_with_no_first_party_methods(
    provider: str, tmp_path: Path
) -> None:
    result = _check(
        {"DJANGO_OAUTH_PROVIDERS": provider, **PROVIDERS[provider]},
        tmp_path / "db.sqlite3",
    )

    assert result.returncode == 0, result.stdout + result.stderr


def test_a_project_with_nothing_enabled_still_starts(tmp_path: Path) -> None:
    """The starter has to be usable before anybody turns authentication on."""
    result = _check({}, tmp_path / "db.sqlite3")

    assert result.returncode == 0, result.stdout + result.stderr
    assert "System check identified no issues" in result.stdout


def test_nothing_enabled_installs_no_authentication_tables(tmp_path: Path) -> None:
    """An unused feature should not cost a migration."""
    result = _run(["manage.py", "migrate", "--plan"], {}, tmp_path / "db.sqlite3")

    assert result.returncode == 0, result.stdout + result.stderr
    assert "auth_core" not in result.stdout
    assert "oauth_core" not in result.stdout


@pytest.mark.parametrize("method", METHODS)
def test_a_single_method_installs_only_what_it_needs(method: str, tmp_path: Path) -> None:
    """Enabling one method must not drag the other three into the schema."""
    result = _run(
        ["manage.py", "migrate", "--plan"],
        {"DJANGO_AUTH_METHODS": method},
        tmp_path / "db.sqlite3",
    )

    assert result.returncode == 0, result.stdout + result.stderr
    for other in METHODS:
        if other != method:
            assert f"auth_{other}" not in result.stdout, other


def test_an_unknown_method_is_refused_at_startup(tmp_path: Path) -> None:
    """Better than silently ignoring a typo and serving no route for it."""
    result = _check({"DJANGO_AUTH_METHODS": "passwrod"}, tmp_path / "db.sqlite3")

    assert result.returncode != 0
    assert "Unknown DJANGO_AUTH_METHODS: passwrod" in result.stderr


def test_a_token_mode_can_be_chosen_without_any_oauth_provider(tmp_path: Path) -> None:
    """DJANGO_OAUTH_MODE names credential tables, not a dependency on social login."""
    result = _check(
        {"DJANGO_AUTH_METHODS": "password", "DJANGO_AUTH_TOKEN_MODE": "session"},
        tmp_path / "db.sqlite3",
    )

    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize("app", FEATURE_APPS)
def test_one_feature_app_installs_with_no_authentication_at_all(app: str, tmp_path: Path) -> None:
    """A feature app is not part of the login story and must not require one.

    Each of these asks the project's own bearer auth who the caller is, and
    support's socket asks the same question of a token -- all behind an
    ImportError guard, so a project that enabled one feature app and nothing
    else has to boot rather than fail importing apps it never turned on.

    Parametrised rather than written out per app, because the interesting case
    is always the app somebody adds next: a new entry in ``FEATURE_APPS`` is
    covered by this file the day it is added, rather than the day somebody
    remembers to copy a test.
    """
    result = _check({_enabled(app): "true"}, tmp_path / "db.sqlite3")

    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize("app", FEATURE_APPS)
def test_a_feature_app_left_unnamed_costs_no_tables(app: str, tmp_path: Path) -> None:
    """Deleting the directory has to be as available as never enabling it.

    Which means an app nobody named leaves nothing behind in the schema -- no
    table, and so nothing to migrate away from later.
    """
    result = _run(["manage.py", "migrate", "--plan"], {}, tmp_path / "db.sqlite3")

    assert result.returncode == 0, result.stdout + result.stderr
    assert app not in result.stdout


@pytest.mark.parametrize("app", FEATURE_APPS)
def test_a_feature_app_installs_its_own_tables_and_no_others(app: str, tmp_path: Path) -> None:
    """Turning one on brings one in.

    The cheap failure this catches is a feature app importing another feature
    app's models at module scope: the project would still boot, and a developer
    who enabled the shop would quietly get the support desk's tables too.
    """
    result = _run(
        ["manage.py", "migrate", "--plan"], {_enabled(app): "true"}, tmp_path / "db.sqlite3"
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert app in result.stdout, f"enabling {app} installed none of its own tables"
    for other in FEATURE_APPS:
        if other != app:
            assert other not in result.stdout, f"enabling {app} dragged in {other}"


def test_support_alone_carries_a_conversation(tmp_path: Path) -> None:
    """Booting is not the claim. The claim is that the app works.

    Support gets this deeper treatment because it is the app with the most ways
    to fail quietly in a bare project: a router whose auth comes from the login
    apps, a socket that resolves a credential through them, and an optional
    hand-off to the notification app. So this opens a ticket over HTTP and then
    a socket over the same Django session, in a project that installed no login
    app and no notification app -- the configuration the main suite, which turns
    everything on, can never reach.
    """
    _drive("support_alone", {"DJANGO_SUPPORT_ENABLED": "true"}, tmp_path / "db.sqlite3")

