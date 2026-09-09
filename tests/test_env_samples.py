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

import re
from pathlib import Path

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


@pytest.mark.parametrize(
    "sample", [STARTER_SAMPLE, TEMPLATE_SAMPLE, EXAMPLE_SAMPLE], ids=lambda p: p.name
)
def test_every_setting_the_project_reads_is_named_in_every_sample(sample: Path) -> None:
    """Read off `base.py` rather than listed here, so a new setting fails this.

    Only the feature apps' own settings: the auth and OAuth apps are configured
    per deployment and their samples are curated on purpose.
    """
    pattern = rf'os\.getenv\(\s*"(DJANGO_(?:{"|".join(FEATURE_APPS)})_\w+)"'
    declared = set(re.findall(pattern, SETTINGS.read_text()))
    assert declared, "no feature-app settings found -- has base.py moved?"

    named = set(re.findall(r"^#?\s*(DJANGO_\w+)=", sample.read_text(), re.MULTILINE))
    missing = sorted(declared - named)

    assert not missing, f"{sample.name} documents none of: {missing}"
