"""Export and import: content that can leave the database it was typed into."""

import json
from io import StringIO
from pathlib import Path
from typing import Any

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError

from apps.cms.models import Field, Menu, Page, Section, SectionPlacement, SiteSettings

pytestmark = pytest.mark.django_db


def export(**options: Any) -> dict[str, Any]:
    out = StringIO()
    call_command("cms_export", stdout=out, **options)
    return json.loads(out.getvalue())


def test_the_export_carries_content_and_no_ids(
    home_with_footer: Page, site: SiteSettings, main_menu: Menu
) -> None:
    """Slugs travel between databases; primary keys do not."""
    document = export()

    assert document["version"] == 1
    assert [page["slug"] for page in document["pages"]] == ["home"]
    assert "id" not in json.dumps(document["pages"])
    assert document["site"]["name"]["fa"] == "آکمی"
    assert [section["slug"] for section in document["library"]] == ["footer"]
    assert document["pages"][0]["shared"] == [{"section": "footer", "order": 99, "is_active": True}]
    assert document["menus"][0]["items"][0]["page"] == "home"


def test_every_language_of_every_value_survives(home: Page) -> None:
    [page] = export()["pages"]
    headline = page["sections"][0]["fields"][0]

    assert headline["values"] == {"en-us": "Welcome", "fa": "خوش آمدید"}


def test_it_writes_a_file_when_asked(home: Page, tmp_path: Path) -> None:
    target = tmp_path / "nested" / "content.json"

    call_command("cms_export", output=str(target), stdout=StringIO())

    assert json.loads(target.read_text())["pages"][0]["slug"] == "home"


class TestImport:
    def test_a_round_trip_reproduces_the_content(
        self, home_with_footer: Page, site: SiteSettings, main_menu: Menu, tmp_path: Path
    ) -> None:
        document = tmp_path / "content.json"
        call_command("cms_export", output=str(document), stdout=StringIO())
        Page.objects.all().delete()
        Section.objects.all().delete()
        Menu.objects.all().delete()

        call_command("cms_import", str(document), stdout=StringIO())

        page = Page.objects.get(slug="home")
        assert page.status == "published"
        assert Field.objects.get(slug="headline").values["fa"] == "خوش آمدید"
        assert SectionPlacement.objects.filter(page=page, section__slug="footer").exists()
        menu = Menu.objects.get(slug="main")
        assert menu.items.filter(parent__isnull=True).count() == 2
        assert menu.items.count() == 3  # the child entry came back too

    def test_importing_twice_changes_nothing_the_second_time(
        self, home: Page, tmp_path: Path
    ) -> None:
        """Matched by slug, so a re-run is an update and not a duplicate."""
        document = tmp_path / "content.json"
        call_command("cms_export", output=str(document), stdout=StringIO())

        call_command("cms_import", str(document), stdout=StringIO())
        call_command("cms_import", str(document), stdout=StringIO())

        assert Page.objects.count() == 1
        assert Section.objects.filter(page__slug="home").count() == 3

    def test_it_updates_what_it_finds_rather_than_replacing_it(
        self, home: Page, tmp_path: Path
    ) -> None:
        document = tmp_path / "content.json"
        call_command("cms_export", output=str(document), stdout=StringIO())
        Field.objects.filter(slug="headline").update(values={"en-us": "Changed locally"})

        call_command("cms_import", str(document), stdout=StringIO())

        assert Field.objects.get(slug="headline").values["en-us"] == "Welcome"

    def test_a_page_the_document_does_not_mention_survives(
        self, home: Page, tmp_path: Path
    ) -> None:
        document = tmp_path / "content.json"
        call_command("cms_export", output=str(document), stdout=StringIO())
        Page.objects.create(name="Local", slug="local")

        call_command("cms_import", str(document), stdout=StringIO())

        assert Page.objects.filter(slug="local").exists()

    def test_pruning_removes_it_and_says_so_in_the_flag(self, home: Page, tmp_path: Path) -> None:
        document = tmp_path / "content.json"
        call_command("cms_export", output=str(document), stdout=StringIO())
        Page.objects.create(name="Local", slug="local")

        call_command("cms_import", str(document), prune=True, stdout=StringIO())

        assert not Page.objects.filter(slug="local").exists()

    def test_a_value_of_the_wrong_shape_is_refused_whole(self, home: Page, tmp_path: Path) -> None:
        """One bad field must not leave half a page imported."""
        document = tmp_path / "content.json"
        call_command("cms_export", output=str(document), stdout=StringIO())
        payload = json.loads(document.read_text())
        payload["pages"].append(
            {
                "slug": "broken",
                "name": "Broken",
                "sections": [
                    {
                        "slug": "hero",
                        "name": "Hero",
                        "fields": [
                            {
                                "slug": "shot",
                                "name": "Shot",
                                "field_type": "image",
                                "values": {"en-us": "not a url"},
                            }
                        ],
                    }
                ],
            }
        )
        document.write_text(json.dumps(payload))

        with pytest.raises(CommandError, match="Expected a URL"):
            call_command("cms_import", str(document), stdout=StringIO())

        assert not Page.objects.filter(slug="broken").exists()

    def test_a_missing_file_is_a_message_and_not_a_traceback(self, tmp_path: Path) -> None:
        with pytest.raises(CommandError, match="Cannot read"):
            call_command("cms_import", str(tmp_path / "nope.json"), stdout=StringIO())

    def test_placing_a_library_section_the_document_lacks_is_refused(
        self, home: Page, tmp_path: Path
    ) -> None:
        document = tmp_path / "content.json"
        call_command("cms_export", output=str(document), stdout=StringIO())
        payload = json.loads(document.read_text())
        payload["pages"][0]["shared"] = [{"section": "ghost", "order": 1}]
        document.write_text(json.dumps(payload))

        with pytest.raises(CommandError, match="ghost"):
            call_command("cms_import", str(document), stdout=StringIO())
