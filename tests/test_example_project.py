"""The example project is a test app, not a decoration.

``examples/build.py`` produces a project the way a reader would: the packaged
generator writes the tree, the example ``.env`` turns every app on, and the
project's own ``startapi`` registers a feature API at two versions. That makes
it the only place where the generator, the template, the settings contracts, the
registry and a feature app are exercised together, from outside, against the
defaults a user actually gets.

These are slow -- each one builds a project and runs a suite inside it -- and
they are the tests that notice when the package ships something the docs
promise and the template no longer has.
"""

import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "examples"))

import build  # noqa: E402

pytestmark = pytest.mark.slow

REPORTED_KEYS = (
    "methods",
    "factors",
    "providers",
    "token_mode",
    "versions",
    "cms",
    "notifications",
    "socket",
)


@pytest.fixture(scope="module")
def example_project(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """One built project for the whole module: building it is the slow part."""
    return build.build(tmp_path_factory.mktemp("example") / "example-api")


def _run(project: Path, *arguments: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, *arguments],
        cwd=project,
        check=False,
        capture_output=True,
        text=True,
        env=build.pristine_environment(),
    )


def test_the_example_project_has_every_app_on(example_project: Path) -> None:
    """The .env next to the walkthrough is what installs them, so read it back."""
    result = _run(example_project, "manage.py", "shell", "-c", INSTALLED_REPORT)

    assert result.returncode == 0, result.stderr
    # `shell -c` prints the settings-contract warnings too; take only our lines.
    reported = dict(
        line.split("=", 1)
        for line in result.stdout.splitlines()
        if line.split("=", 1)[0] in REPORTED_KEYS
    )
    assert reported["methods"] == "password,email_code,sms_code,magic_link"
    assert reported["factors"] == "totp,sms,email,recovery"
    assert reported["providers"] == "google,apple,microsoft,github"
    assert reported["token_mode"] == "rotation"
    assert reported["versions"] == "v1,v2"
    assert reported["cms"] == "on"
    assert reported["notifications"] == "on"
    assert reported["socket"] == "/ws/notifications"


INSTALLED_REPORT = """
from django.conf import settings
from config.sockets import websocket_routes
from infrastructure.common.registry import load_api_registry

print("methods=" + ",".join(settings.AUTH_METHODS))
print("factors=" + ",".join(settings.AUTH_SECOND_FACTORS))
print("providers=" + ",".join(settings.OAUTH_PROVIDERS))
print("token_mode=" + settings.AUTH_TOKEN_MODE)
print("versions=" + ",".join(load_api_registry()))
print("cms=" + ("on" if settings.CMS_ENABLED else "off"))
print("notifications=" + ("on" if settings.NOTIFICATIONS_ENABLED else "off"))
print("socket=" + ",".join(path for path, _ in websocket_routes()))
"""


def test_the_example_project_passes_its_own_checks(example_project: Path) -> None:
    """Warnings the example admits to -- no Redis, no carrier -- but never an error."""
    result = _run(example_project, "manage.py", "check")

    assert result.returncode == 0, result.stdout + result.stderr
    assert "ERRORS" not in result.stdout


def test_the_example_project_has_no_unmade_migrations(example_project: Path) -> None:
    """The notes migration is committed, so it has to still match its models."""
    result = _run(example_project, "manage.py", "makemigrations", "--check", "--dry-run")

    assert result.returncode == 0, result.stdout + result.stderr


def test_the_example_project_passes_its_own_suite(example_project: Path) -> None:
    """Including the notes tests, which only ever run here."""
    result = _run(example_project, "-m", "pytest", "-q", "--no-cov")

    assert result.returncode == 0, result.stdout + result.stderr


def test_the_notes_feature_app_is_covered_by_that_suite(example_project: Path) -> None:
    result = _run(example_project, "-m", "pytest", "-q", "--no-cov", "src/apps/notes")

    assert result.returncode == 0, result.stdout + result.stderr
    assert "no tests ran" not in result.stdout


def test_the_cms_ships_with_the_generated_project(example_project: Path) -> None:
    """It comes from the template rather than from `startapi`, so nothing else proves it."""
    result = _run(example_project, "-m", "pytest", "-q", "--no-cov", "src/apps/cms")

    assert result.returncode == 0, result.stdout + result.stderr
    assert "no tests ran" not in result.stdout


def test_the_notifications_app_ships_with_the_generated_project(example_project: Path) -> None:
    """The socket half of it runs nowhere else: `runserver` is WSGI and cannot serve it."""
    result = _run(example_project, "-m", "pytest", "-q", "--no-cov", "src/apps/notifications")

    assert result.returncode == 0, result.stdout + result.stderr
    assert "no tests ran" not in result.stdout


def test_the_walkthrough_visits_every_app(example_project: Path) -> None:
    """The tour asserts its own status codes, so a zero exit is the assertion."""
    result = subprocess.run(
        [sys.executable, str(build.EXAMPLES / "walkthrough.py"), "--project", str(example_project)],
        check=False,
        capture_output=True,
        text=True,
        env=build.pristine_environment(),
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert "Every installed app answered." in result.stdout
    assert "/api/v2/notes/" in result.stdout, "the feature app's second version was not toured"
    assert "/api/v1/cms/pages/home" in result.stdout, "the content app was not toured"
    assert "/api/v1/notifications" in result.stdout, "the notifications API was not toured"
    assert "/ws/notifications" in result.stdout, "the notification socket was not toured"


def test_building_twice_into_the_same_place_is_refused(tmp_path: Path) -> None:
    """Half-overwriting somebody's project is worse than making them say --rebuild."""
    destination = tmp_path / "example-api"
    build.build(destination)

    with pytest.raises(FileExistsError):
        build.build(destination)

    assert build.build(destination, rebuild=True) == destination.resolve()
