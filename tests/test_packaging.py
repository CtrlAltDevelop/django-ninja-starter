"""What the published distribution promises about itself.

These are the mistakes that only surface after a release: a version that says one
thing in the metadata and another in the module, a template file that never made
it into the wheel, or a runtime dependency added to the reference project and
forgotten in the template that ships to users.
"""

import re
import tomllib
from pathlib import Path

import pytest

import django_ninja_starter
from django_ninja_starter.cli import _IGNORED_TEMPLATE_PARTS

ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = ROOT / "src" / "django_ninja_starter" / "template"


def shipped_files() -> list[Path]:
    """Every template file the CLI would actually copy.

    Local tool caches live under the template directory too. Skipping exactly
    what the CLI skips is what makes these assertions about the shipped tree
    rather than about somebody's working copy.
    """
    return [
        path
        for path in TEMPLATE.rglob("*")
        if path.is_file() and not set(path.relative_to(TEMPLATE).parts) & _IGNORED_TEMPLATE_PARTS
    ]


def _pyproject(path: Path) -> dict:
    with path.open("rb") as handle:
        data: dict = tomllib.load(handle)
    return data


@pytest.fixture(scope="module")
def generator() -> dict:
    return _pyproject(ROOT / "pyproject.toml")


@pytest.fixture(scope="module")
def template() -> dict:
    return _pyproject(TEMPLATE / "pyproject.toml")


def test_the_distribution_version_comes_from_the_module(generator: dict) -> None:
    """One place states the version, and the build is pointed at it.

    A static ``version`` here would be a second statement of the same fact, and a
    release with the two disagreeing reports one number to PyPI and another to
    ``--version``.
    """
    assert "version" not in generator["project"]
    assert "version" in generator["project"]["dynamic"]
    assert generator["tool"]["setuptools"]["dynamic"]["version"] == {
        "attr": "django_ninja_starter.__version__"
    }
    assert re.fullmatch(r"\d+\.\d+\.\d+", django_ninja_starter.__version__)


def test_the_generator_itself_needs_nothing_installed(generator: dict) -> None:
    """It copies a tree and substitutes two placeholders. Installing it should
    never drag Django into an environment that only wanted the command."""
    assert generator["project"]["dependencies"] == []


def test_the_template_is_declared_as_package_data(generator: dict) -> None:
    """Without this the wheel ships a CLI with nothing for it to copy."""
    package_data = generator["tool"]["setuptools"]["package-data"]["django_ninja_starter"]

    assert "template/**/*" in package_data
    assert "py.typed" in package_data


def test_every_runtime_import_the_template_makes_is_declared(template: dict) -> None:
    """A dependency used by the shipped code but named nowhere in its metadata is
    a project that installs cleanly and fails on first request."""
    declared = " ".join(
        [
            *template["project"]["dependencies"],
            *(
                requirement
                for group in template["project"]["optional-dependencies"].values()
                for requirement in group
            ),
        ]
    ).lower()

    for distribution in (
        "django",
        "django-ninja",
        "pyjwt",
        "cryptography",
        "httpx",
        "pyotp",
        "redis",
    ):
        assert distribution in declared, distribution


def test_the_optional_extras_are_reachable_through_all(template: dict) -> None:
    extras = template["project"]["optional-dependencies"]
    combined = " ".join(extras["all"])

    for name in ("oauth", "redis", "totp"):
        assert name in extras
        assert name in combined


def test_the_development_extra_pulls_in_every_feature(template: dict) -> None:
    """Otherwise the generated project's own test suite cannot import half of it."""
    assert any(
        "[all]" in requirement
        for requirement in template["project"]["optional-dependencies"]["dev"]
    )


def test_the_reference_project_and_the_template_agree_on_versions() -> None:
    """The repository develops against the same pins it ships.

    Both files name the same distributions; only where they are declared differs,
    since the generator's own extras are a development environment rather than a
    dependency set. A drift here means the suite is proving something about a
    version nobody will install.
    """
    generator = _pyproject(ROOT / "pyproject.toml")
    template = _pyproject(TEMPLATE / "pyproject.toml")

    def pins(requirements: list[str]) -> dict[str, str]:
        found = {}
        for requirement in requirements:
            if "[" in requirement or ">" not in requirement:
                continue
            name, _, rest = requirement.partition(">")
            found[name.strip().lower()] = f">{rest}"
        return found

    shipped = pins(
        [
            *template["project"]["dependencies"],
            *(
                requirement
                for group in template["project"]["optional-dependencies"].values()
                for requirement in group
            ),
        ]
    )
    developed = pins(generator["project"]["optional-dependencies"]["dev"])

    disagreements = {
        name: (shipped[name], developed[name])
        for name in shipped.keys() & developed.keys()
        if shipped[name] != developed[name]
    }

    assert disagreements == {}


def test_the_template_carries_no_build_artefacts() -> None:
    """Anything here is copied verbatim into somebody's new project."""
    strays = [
        str(path.relative_to(TEMPLATE))
        for path in shipped_files()
        if path.suffix in {".pyc", ".pyo"} or path.name == ".DS_Store"
    ]

    assert strays == []


def test_the_template_holds_no_unrendered_placeholder_outside_the_known_two() -> None:
    """The CLI only substitutes two names; a third would ship as literal braces.

    Django templates are exempt, and only they: their braces are addressed to
    Django at render time, not to the generator at copy time. The CLI replaces
    two literal strings and leaves everything else alone, so the two never meet.
    """
    known = {"{{ project_name }}", "{{ project_title }}"}
    found = set()
    for path in shipped_files():
        if "templates" in path.parts:
            continue
        text = path.read_text(encoding="utf-8")
        start = 0
        while (start := text.find("{{", start)) != -1:
            end = text.find("}}", start)
            if end == -1:
                break
            found.add(text[start : end + 2])
            start = end + 2

    assert found <= known, found - known


def _git_remote() -> str | None:
    """The repository this working copy actually points at, if git can say."""
    import subprocess

    try:
        result = subprocess.run(
            ["git", "remote", "get-url", "origin"],
            cwd=ROOT,
            check=False,
            capture_output=True,
            text=True,
        )
    except OSError:  # pragma: no cover - git absent
        return None
    url = result.stdout.strip()
    return url.removesuffix(".git") if result.returncode == 0 and url else None


def test_the_published_urls_match_the_repository_they_describe(generator: dict) -> None:
    """Metadata URLs are the one thing nobody notices is wrong until after a release.

    Pinned to the git remote rather than to a literal, so a fork that changes its
    remote is told to change its metadata too instead of quietly publishing links
    to somebody else's repository.
    """
    remote = _git_remote()
    if remote is None or not remote.startswith("https://github.com/"):
        pytest.skip("no https origin to compare against")

    urls = generator["project"]["urls"]

    assert urls["Homepage"] == remote
    assert urls["Repository"] == remote
    for name in ("Issues", "Changelog", "Documentation"):
        assert urls[name].startswith(f"{remote}/"), name


def test_every_published_url_points_at_something_that_exists(generator: dict) -> None:
    """The paths inside the repository, which a typo would otherwise 404 forever."""
    urls = generator["project"]["urls"]

    assert (ROOT / "CHANGELOG.md").is_file(), urls["Changelog"]
    assert (ROOT / "docs").is_dir(), urls["Documentation"]
