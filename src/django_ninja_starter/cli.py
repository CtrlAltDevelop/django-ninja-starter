"""Command-line interface for creating a Django Ninja Starter project."""

import argparse
import re
from importlib.resources import files
from importlib.resources.abc import Traversable
from pathlib import Path
from typing import Final

from django_ninja_starter import __version__

_VALID_PROJECT_NAME: Final = re.compile(r"^[A-Za-z][A-Za-z0-9._-]*$")
_RENAMED_PARTS: Final = {
    "_env.example": ".env.example",
    "_github": ".github",
    "_gitignore": ".gitignore",
}
_IGNORED_TEMPLATE_PARTS: Final = {
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    "__pycache__",
}


def _project_title(name: str) -> str:
    return " ".join(part.capitalize() for part in re.split(r"[-_.]+", name))


def _output_path(relative_path: Path) -> Path:
    return Path(*(_RENAMED_PARTS.get(part, part) for part in relative_path.parts))


def create_project(name: str, destination: Path | None = None) -> Path:
    """Create a starter project and return its absolute destination."""
    if not _VALID_PROJECT_NAME.fullmatch(name):
        raise ValueError(
            "project name must start with a letter and contain only letters, "
            "numbers, dots, underscores, or hyphens"
        )

    target = (destination or Path(name)).expanduser().resolve()
    if target.exists() and (not target.is_dir() or any(target.iterdir())):
        raise FileExistsError(f"destination is not empty: {target}")

    target.mkdir(parents=True, exist_ok=True)
    replacements = {
        "{{ project_name }}": name.lower().replace("_", "-"),
        "{{ project_title }}": _project_title(name),
    }
    template_root = files("django_ninja_starter").joinpath("template")

    def copy_directory(source: Traversable, relative: Path = Path()) -> None:
        for item in source.iterdir():
            if item.name in _IGNORED_TEMPLATE_PARTS:
                continue
            item_relative = relative / item.name
            if item.is_dir():
                copy_directory(item, item_relative)
                continue

            output = target / _output_path(item_relative)
            output.parent.mkdir(parents=True, exist_ok=True)
            content = item.read_text(encoding="utf-8")
            for placeholder, value in replacements.items():
                content = content.replace(placeholder, value)
            output.write_text(content, encoding="utf-8")

    copy_directory(template_root)
    return target


def build_parser() -> argparse.ArgumentParser:
    """Build the command-line argument parser."""
    parser = argparse.ArgumentParser(
        prog="django-ninja-starter",
        description="Create a production-oriented Django Ninja API project.",
    )
    parser.add_argument("project_name", help="project/distribution name, for example billing-api")
    parser.add_argument(
        "--directory",
        type=Path,
        help="output directory (defaults to ./PROJECT_NAME)",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    return parser


def main() -> None:
    """Generate a project from command-line arguments."""
    parser = build_parser()
    args = parser.parse_args()
    try:
        target = create_project(args.project_name, args.directory)
    except (FileExistsError, ValueError) as error:
        parser.error(str(error))

    print(f"Created {args.project_name} in {target}")
    print(f"Next: cd {target} && python3 -m venv .venv && make install")
