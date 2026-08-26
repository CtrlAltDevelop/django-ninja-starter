"""The documentation generator, and whether the checked-in pages are current.

The point of generating the reference sections is that they cannot rot. That only
holds if something fails when they do, which is what the drift test below is for:
change a route, a model, an admin registration or a settings declaration without
regenerating, and the suite says so.
"""

from pathlib import Path

import pytest
from django.conf import settings
from django.core.management import call_command
from django.core.management.base import CommandError

from infrastructure.common.management.commands.authdocs import (
    SECTIONS,
    documented_apps,
)

DOCS = Path(settings.BASE_DIR) / "docs"


def test_the_checked_in_documentation_is_up_to_date() -> None:
    """Fails when a page no longer matches the code it describes.

    If this fails, run:
        DJANGO_SETTINGS_MODULE=config.settings.test python manage.py authdocs
    """
    call_command("authdocs", check=True)


def test_every_app_has_a_page() -> None:
    missing = [app.label for app in documented_apps(DOCS) if not app.path.exists()]

    assert missing == []


@pytest.mark.parametrize("section", SECTIONS)
def test_every_page_carries_every_generated_section(section: str) -> None:
    """A page missing a block would silently document three things out of four."""
    without = [
        str(app.path.relative_to(DOCS))
        for app in documented_apps(DOCS)
        if f"<!-- generated:{section} -->" not in app.path.read_text(encoding="utf-8")
    ]

    assert without == []


def test_every_page_says_more_than_the_generator_wrote() -> None:
    """A page with no prose is a table nobody can act on."""
    thin = []
    for app in documented_apps(DOCS):
        text = app.path.read_text(encoding="utf-8")
        for section in SECTIONS:
            opening = f"<!-- generated:{section} -->"
            closing = f"<!-- /generated:{section} -->"
            start, end = text.index(opening), text.index(closing) + len(closing)
            text = text[:start] + text[end:]
        if len(text.split()) < 120:
            thin.append(str(app.path.relative_to(DOCS)))

    assert thin == []


def test_every_page_is_reachable_from_the_index() -> None:
    """An unlinked page is one nobody finds."""
    index = (DOCS / "README.md").read_text(encoding="utf-8")

    unlinked = [
        str(app.path.relative_to(DOCS))
        for app in documented_apps(DOCS)
        if str(app.path.relative_to(DOCS)) not in index
    ]

    assert unlinked == []


def test_every_internal_link_points_at_something() -> None:
    """Relative links between the pages, which nothing else would catch."""
    broken = []
    for page in sorted(DOCS.rglob("*.md")):
        text = page.read_text(encoding="utf-8")
        start = 0
        while (start := text.find("](", start)) != -1:
            end = text.find(")", start)
            if end == -1:
                break
            target = text[start + 2 : end]
            start = end + 1
            if target.startswith(("http://", "https://", "#")):
                continue
            path, _, _anchor = target.partition("#")
            if path and not (page.parent / path).resolve().exists():
                broken.append(f"{page.relative_to(DOCS)} -> {target}")

    assert broken == []


def test_a_page_missing_its_markers_is_refused_rather_than_overwritten(
    tmp_path: Path,
) -> None:
    """Prose is not something a generator should be allowed to eat."""
    for app in documented_apps(tmp_path):
        app.path.parent.mkdir(parents=True, exist_ok=True)
        app.path.write_text("# Hand-written, no markers\n", encoding="utf-8")
        break

    with pytest.raises(CommandError, match="has no 'routes' block"):
        call_command("authdocs", docs_root=tmp_path)


def test_generating_into_an_empty_tree_scaffolds_every_page(tmp_path: Path) -> None:
    call_command("authdocs", docs_root=tmp_path)

    written = sorted(path.relative_to(tmp_path).as_posix() for path in tmp_path.rglob("*.md"))

    assert written == sorted(
        app.path.relative_to(tmp_path).as_posix() for app in documented_apps(tmp_path)
    )


def test_check_mode_reports_drift_without_writing(tmp_path: Path) -> None:
    call_command("authdocs", docs_root=tmp_path)
    page = next(app.path for app in documented_apps(tmp_path))
    original = page.read_text(encoding="utf-8")
    page.write_text(
        original.replace("<!-- generated:routes -->", "<!-- generated:routes -->\nstale nonsense"),
        encoding="utf-8",
    )

    with pytest.raises(CommandError, match="out of date"):
        call_command("authdocs", "--check", docs_root=tmp_path)

    assert "stale nonsense" in page.read_text(encoding="utf-8")
