import os
import subprocess
import sys
from pathlib import Path

import pytest

from django_ninja_starter.cli import create_project, main


def test_create_project_renders_complete_starter(tmp_path: Path) -> None:
    target = create_project("billing-api", tmp_path / "generated")

    assert target == (tmp_path / "generated").resolve()
    assert (target / ".env.example").is_file()
    assert (target / ".github/workflows/ci.yml").is_file()
    assert (target / "src/apps/__init__.py").is_file()
    assert (target / "src/infrastructure/common/api.py").is_file()
    assert 'name = "billing-api"' in (target / "pyproject.toml").read_text()
    assert 'title="Billing Api API"' in (target / "src/config/api.py").read_text()

    generated_text = "\n".join(
        path.read_text()
        for path in target.rglob("*")
        if path.is_file() and path.suffix not in {".pyc"}
    )
    assert "{{ project_" not in generated_text


def _pristine_environment() -> dict[str, str]:
    """Return the environment stripped of this suite's own Django configuration.

    ``config.settings.test`` configures itself through ``os.environ.setdefault``,
    which mutates the real process environment. A subprocess would inherit it and
    the generated project would be checked against this repository's test setup
    rather than its own defaults.
    """
    return {
        key: value
        for key, value in os.environ.items()
        if not key.startswith(("DJANGO_", "GOOGLE_", "APPLE_", "MICROSOFT_", "GITHUB_"))
    }


def test_generated_project_passes_django_check(tmp_path: Path) -> None:
    target = create_project("smoke-test", tmp_path / "smoke-test")

    result = subprocess.run(
        [sys.executable, "manage.py", "check"],
        cwd=target,
        check=False,
        capture_output=True,
        text=True,
        env=_pristine_environment(),
    )

    assert result.returncode == 0, result.stderr
    assert "System check identified no issues" in result.stdout


def test_generated_project_runs_its_own_tests(tmp_path: Path) -> None:
    """The starter is only useful if what it emits is green out of the box."""
    target = create_project("smoke-test", tmp_path / "smoke-test")

    result = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "--no-cov"],
        cwd=target,
        check=False,
        capture_output=True,
        text=True,
        env=_pristine_environment(),
    )

    assert result.returncode == 0, result.stdout + result.stderr


def test_a_new_project_carries_no_optional_app(tmp_path: Path) -> None:
    """Nothing optional is installed by being shipped, the CMS included.

    A generated project runs the content app's migrations, publishes its routes
    and shows its admin only once somebody names it -- the same contract every
    login method and provider has.
    """
    target = create_project("bare", tmp_path / "bare")

    result = subprocess.run(
        [sys.executable, "manage.py", "shell", "-c", INSTALLED_REPORT],
        cwd=target,
        check=False,
        capture_output=True,
        text=True,
        env={**_pristine_environment(), "DJANGO_SETTINGS_MODULE": "config.settings.development"},
    )

    assert result.returncode == 0, result.stderr
    reported = dict(
        line.split("=", 1) for line in result.stdout.splitlines() if "=" in line.split(" ")[0]
    )
    assert reported["cms"] == "off"
    assert reported["cms_routes"] == "0"
    assert reported["methods"] == ""


INSTALLED_REPORT = """
from django.conf import settings

print("cms=" + ("on" if settings.CMS_ENABLED else "off"))
print("cms_routes=" + str(len(settings.CMS_ROUTERS)))
print("methods=" + ",".join(settings.AUTH_METHODS))
"""


@pytest.mark.parametrize("name", ["", "123-api", "spaces are invalid", "bad/name"])
def test_create_project_rejects_invalid_names(tmp_path: Path, name: str) -> None:
    with pytest.raises(ValueError, match="project name"):
        create_project(name, tmp_path / "output")


def test_create_project_refuses_nonempty_destination(tmp_path: Path) -> None:
    target = tmp_path / "existing"
    target.mkdir()
    (target / "keep.txt").write_text("user content")

    with pytest.raises(FileExistsError, match="destination is not empty"):
        create_project("valid-name", target)


def test_main_creates_project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    target = tmp_path / "custom-output"
    monkeypatch.setattr(
        sys,
        "argv",
        ["django-ninja-starter", "sample_api", "--directory", str(target)],
    )

    main()

    assert (target / "manage.py").is_file()
