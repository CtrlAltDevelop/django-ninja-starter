#!/usr/bin/env python3
"""Build the example project by running the generator this repository ships.

    python examples/build.py                 # into examples/.build/example-api
    python examples/build.py --into ./demo   # somewhere of your choosing
    python examples/build.py --rebuild       # discard and start over

Nothing here assembles a project by hand. The tree is produced by
``create_project`` -- the same call the ``django-ninja-starter`` command makes,
taken from an installed copy of the package when there is one and from this
checkout otherwise -- then configured and extended exactly as its README tells a
reader to configure and extend it:

1.  ``examples/env.example`` is copied in as the project's ``.env``, which is
    what turns every login method, second factor, social provider and token
    mode on, along with all four feature apps the template ships -- the CMS,
    notifications, the shop and the support desk, whose tables, routes and
    WebSockets exist only once they are named.
2.  ``manage.py startapi notes`` scaffolds and registers a feature API at v1 and
    again at v2, so the registry entries below are written by the project's own
    command rather than by hand.
3.  ``examples/notes/`` is copied over that scaffolding, replacing the placeholder
    with a real feature app: a model, an admin, one service, two REST versions,
    a GraphQL contribution, a set of gRPC actions, and their tests.
4.  ``manage.py protos`` compiles the notes app's ``.proto`` and its stubs, the
    same command a developer runs after touching a ``@grpc_action``.
5.  ``manage.py migrate`` leaves a database behind, so ``make run`` in the built
    project serves the API immediately.

The result is a project a reader could have produced themselves, and the thing
``examples/walkthrough.py`` and ``tests/test_example_project.py`` both run
against. When the generator or the template breaks, this is what notices.
"""

import argparse
import os
import shutil
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Final

REPO_ROOT: Final = Path(__file__).resolve().parents[1]
EXAMPLES: Final = Path(__file__).resolve().parent
DEFAULT_DESTINATION: Final = EXAMPLES / ".build" / "example-api"
PROJECT_NAME: Final = "example-api"

# The versions the notes app is registered at. Two of them, because one feature
# app serving two API versions from one set of models is the case a starter has
# to get right and the hardest one to discover from a single-version example.
NOTES_VERSIONS: Final = ("v1", "v2")

_IGNORED: Final = shutil.ignore_patterns("__pycache__", "*.pyc")


def pristine_environment() -> dict[str, str]:
    """Return the environment with this repository's own Django settings removed.

    ``config.settings.test`` configures itself through ``os.environ.setdefault``,
    which mutates the real process environment. A child that inherited it would
    be building the example project against this repository's test setup instead
    of against the ``.env`` the example actually ships.
    """
    return {
        key: value
        for key, value in os.environ.items()
        if not key.startswith(("DJANGO_", "GOOGLE_", "APPLE_", "MICROSOFT_", "GITHUB_"))
    }


def run_manage(project: Path, *arguments: str) -> None:
    """Run one of the built project's own management commands, inside it."""
    result = subprocess.run(
        [sys.executable, "manage.py", *arguments],
        cwd=project,
        check=False,
        capture_output=True,
        text=True,
        env=pristine_environment(),
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"manage.py {' '.join(arguments)} failed in {project}:\n"
            f"{result.stdout}\n{result.stderr}"
        )


def generator() -> Callable[[str, Path | None], Path]:
    """Return the generator, preferring an installed one over this checkout.

    An installed ``django-ninja-starter`` is the package a user would be holding,
    so if one is present it is the one that should be building the example -- CI
    points this at a freshly built wheel for exactly that reason. The fallback
    keeps the example runnable in a clone that has installed nothing yet.
    """
    try:
        from django_ninja_starter.cli import create_project
    except ImportError:
        sys.path.insert(0, str(REPO_ROOT / "src"))
        from django_ninja_starter.cli import create_project
    return create_project


def build(destination: Path = DEFAULT_DESTINATION, *, rebuild: bool = False) -> Path:
    """Create the example project at ``destination`` and return its path."""
    create_project = generator()

    destination = destination.expanduser().resolve()
    if rebuild and destination.exists():
        shutil.rmtree(destination)

    project = create_project(PROJECT_NAME, destination)
    shutil.copyfile(EXAMPLES / "env.example", project / ".env")

    for version in NOTES_VERSIONS:
        run_manage(project, "startapi", "notes", "--api-version", version)
    shutil.copytree(
        EXAMPLES / "notes",
        project / "src" / "apps" / "notes",
        dirs_exist_ok=True,
        ignore=_IGNORED,
    )

    # The notes app arrives with gRPC services and no `.proto`. Compiling them is
    # a step a developer takes too, which is why it is run here rather than the
    # generated stubs being committed alongside the source they came from.
    run_manage(project, "protos")
    run_manage(project, "migrate")
    return project


def ensure(destination: Path = DEFAULT_DESTINATION, *, rebuild: bool = False) -> Path:
    """Build the project unless one is already sitting there."""
    destination = destination.expanduser().resolve()
    if not rebuild and (destination / "manage.py").is_file():
        return destination
    return build(destination, rebuild=rebuild)


def main() -> int:
    parser = argparse.ArgumentParser(
        prog="examples/build.py",
        description="Build the example project with the packaged generator.",
    )
    parser.add_argument(
        "--into",
        type=Path,
        default=DEFAULT_DESTINATION,
        help=f"output directory (defaults to {DEFAULT_DESTINATION.relative_to(REPO_ROOT)})",
    )
    parser.add_argument(
        "--rebuild",
        action="store_true",
        help="delete an existing build first instead of refusing to overwrite it",
    )
    arguments = parser.parse_args()

    try:
        project = build(arguments.into, rebuild=arguments.rebuild)
    except (FileExistsError, RuntimeError) as error:
        print(str(error), file=sys.stderr)
        return 1

    import django_ninja_starter

    print(f"Built {PROJECT_NAME} in {project}")
    print(f"Generated by django-ninja-starter {django_ninja_starter.__version__}")
    print("Every app is on, CMS and notifications included.")
    print("Next: python examples/walkthrough.py")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
