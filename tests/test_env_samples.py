"""What the two `.env` samples promise, which is not the same promise.

They look alike and are meant to differ in exactly one respect, so the
difference is asserted rather than left to whoever edits one of them next.

This repository's own sample turns every feature app on: `make run` here is for
reading the starter, and an app left off is a group missing from `/api/docs`
with nothing on the page to say why. The sample a *generated* project is given
turns them all off, because there the cost runs the other way -- an app nobody
asked for still brings tables, migrations, protos and an admin.

Both samples must name every setting its apps read. A setting that exists and is
documented nowhere is found by reading the source, which is the thing these
files exist to save somebody from.
"""

import importlib.util
import os
import re
from pathlib import Path
from typing import Any
from unittest import mock

import pytest

ROOT = Path(__file__).resolve().parents[1]
STARTER_SAMPLE = ROOT / ".env.example"
TEMPLATE_SAMPLE = ROOT / "src" / "django_ninja_starter" / "template" / "_env.example"
EXAMPLE_SAMPLE = ROOT / "examples" / "env.example"
SETTINGS = ROOT / "src" / "config" / "settings" / "base.py"

FEATURE_APPS = ("CMS", "NOTIFICATIONS", "SHOP", "SUPPORT")


def flag(sample: Path, app: str) -> str | None:
    """What one sample sets `DJANGO_<APP>_ENABLED` to, or ``None`` if it is silent."""
    found = re.search(rf"^DJANGO_{app}_ENABLED=(\S*)", sample.read_text(), re.MULTILINE)
    return found.group(1) if found else None


@pytest.mark.parametrize("app", FEATURE_APPS)
def test_the_starter_sample_turns_every_feature_app_on(app: str) -> None:
    """Whoever copies this file is reading the starter, and wants to see all of it.

    The failure this prevents is quiet: the project boots, the tests pass, and
    `/api/docs` is simply missing a group, which reads as a bug in the app
    rather than as a flag nobody set.
    """
    assert flag(STARTER_SAMPLE, app) == "true", (
        f"{STARTER_SAMPLE.name} leaves {app} off, so `make run` here serves an API "
        "with that group missing from /api/docs and nothing to say why"
    )


@pytest.mark.parametrize("app", FEATURE_APPS)
def test_the_template_sample_ships_every_feature_app_off(app: str) -> None:
    """The opposite promise, and the one the README makes to a generated project."""
    assert flag(TEMPLATE_SAMPLE, app) == "false", (
        f"{TEMPLATE_SAMPLE.name} ships {app} on, so every generated project carries "
        "its tables, migrations and admin whether or not it asked for them"
    )


@pytest.mark.parametrize("app", FEATURE_APPS)
def test_the_example_project_turns_every_feature_app_on(app: str) -> None:
    """The tour cannot tour an app that is not installed."""
    assert flag(EXAMPLE_SAMPLE, app) == "true"


def settings_read_from_the_environment() -> set[str]:
    """Every `DJANGO_*` key `base.py` actually asks the environment for.

    Executed rather than pattern-matched. A regex over the source can only find
    the names spelled as literals, and the moment one is built -- as the
    per-app transport lists are, from `f"DJANGO_{app.upper()}_TRANSPORTS"` --
    it becomes a setting the project reads and this file cannot see. A guard
    with a blind spot is worse than no guard, because it reports success over
    exactly the settings most likely to be undocumented.

    So the module is run with `os.getenv` recording what it is asked for, in a
    fresh namespace and against an environment that turns every feature app on:
    an app left off would take its own settings out of the answer.
    """
    asked: set[str] = set()
    real_getenv = os.getenv

    def recording_getenv(key: str, default: Any = None) -> Any:
        asked.add(key)
        return real_getenv(key, default)

    environment = {
        # No `.env` file: this has to see the settings module's own defaults
        # rather than whatever the developer running the suite has configured.
        "DJANGO_ENV_FILE": "",
        "DJANGO_SECRET_KEY": "env-sample-secret-key-long-enough-for-hs256",
        **{f"DJANGO_{app}_ENABLED": "true" for app in FEATURE_APPS},
    }

    specification = importlib.util.spec_from_file_location("_env_probe_settings", SETTINGS)
    assert specification and specification.loader
    module = importlib.util.module_from_spec(specification)

    with (
        mock.patch.dict(os.environ, environment, clear=True),
        mock.patch("os.getenv", recording_getenv),
    ):
        specification.loader.exec_module(module)

    return {key for key in asked if key.startswith("DJANGO_")}


def test_the_guard_can_see_settings_whose_names_are_built() -> None:
    """The blind spot this file used to have, asserted so it cannot come back.

    `DJANGO_SUPPORT_TRANSPORTS` is never written down in `base.py`; it is built
    from the app's name. If the collector ever goes back to reading the source
    as text, this is the test that says so.
    """
    assert "DJANGO_SUPPORT_TRANSPORTS" in settings_read_from_the_environment()


@pytest.mark.parametrize(
    "sample", [STARTER_SAMPLE, TEMPLATE_SAMPLE, EXAMPLE_SAMPLE], ids=lambda p: p.name
)
def test_every_setting_the_project_reads_is_named_in_every_sample(sample: Path) -> None:
    """Read off what `base.py` asks for, so a new setting fails this.

    Only the feature apps' own settings: the auth and OAuth apps are configured
    per deployment and their samples are curated on purpose.
    """
    prefixes = tuple(f"DJANGO_{app}_" for app in FEATURE_APPS)
    declared = {key for key in settings_read_from_the_environment() if key.startswith(prefixes)}
    assert declared, "no feature-app settings found -- has base.py moved?"

    named = set(re.findall(r"^#?\s*(DJANGO_\w+)=", sample.read_text(), re.MULTILINE))
    missing = sorted(declared - named)

    assert not missing, f"{sample.name} documents none of: {missing}"
