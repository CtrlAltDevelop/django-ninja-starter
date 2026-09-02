"""Library sections and menus: the two things one page cannot own."""

from typing import Any

import pytest
from django.core.exceptions import ValidationError
from django.test import Client

from apps.cms.models import Menu, MenuItem, Page, Section, SectionPlacement

pytestmark = pytest.mark.django_db

PAGES = "/api/v1/cms/pages"
MENUS = "/api/v1/cms/menus"


def data(response: Any) -> Any:
    assert response.status_code == 200, response.content
    return response.json()["data"]


class TestLibrarySections:
    def test_a_shared_section_appears_on_the_page_it_is_placed_on(
        self, client: Client, home_with_footer: Page
    ) -> None:
        sections = data(client.get(f"{PAGES}/home"))["sections"]

        assert [section["id"] for section in sections] == ["hero", "plans", "footer"]
        assert sections[-1]["fields"][0]["value"] == "© Acme"

    def test_it_says_which_bands_are_shared(self, client: Client, home_with_footer: Page) -> None:
        """Rendering does not change; an editing client's warning does."""
        sections = data(client.get(f"{PAGES}/home"))["sections"]

        assert [section["shared"] for section in sections] == [False, False, True]

    def test_placement_order_sorts_it_with_the_page_s_own_sections(
        self, client: Client, home_with_footer: Page, footer: Section
    ) -> None:
        SectionPlacement.objects.filter(section=footer).update(order=0)
        Section.objects.filter(slug="hero").update(order=1)

        sections = data(client.get(f"{PAGES}/home"))["sections"]

        assert [section["id"] for section in sections] == ["footer", "hero", "plans"]

    def test_writing_it_once_changes_every_page_that_placed_it(
        self, client: Client, home_with_footer: Page, footer: Section
    ) -> None:
        other = Page.objects.create(name="About", slug="about", status="published")
        SectionPlacement.objects.create(page=other, section=footer)

        footer.fields.update(values={"en-us": "© Acme 2027"})

        for slug in ("home", "about"):
            sections = data(client.get(f"{PAGES}/{slug}"))["sections"]
            assert sections[-1]["fields"][0]["value"] == "© Acme 2027"

    def test_hiding_the_placement_hides_it_on_that_page_only(
        self, client: Client, home_with_footer: Page, footer: Section
    ) -> None:
        other = Page.objects.create(name="About", slug="about", status="published")
        SectionPlacement.objects.create(page=other, section=footer)
        SectionPlacement.objects.filter(page=home_with_footer).update(is_active=False)

        assert len(data(client.get(f"{PAGES}/home"))["sections"]) == 2
        assert len(data(client.get(f"{PAGES}/about"))["sections"]) == 1

    def test_a_page_s_own_section_cannot_be_placed_elsewhere(
        self, home: Page, footer: Section
    ) -> None:
        """Sharing is a property of the library, not a way to borrow a page's band."""
        hero = Section.objects.get(slug="hero")
        other = Page.objects.create(name="About", slug="about")

        with pytest.raises(ValidationError, match="library section"):
            SectionPlacement.objects.create(page=other, section=hero)

    def test_two_library_sections_cannot_share_a_slug(self, footer: Section) -> None:
        """NULL page means the ordinary unique constraint says nothing at all."""
        with pytest.raises(ValidationError):
            Section.objects.create(page=None, name="Other footer", slug="footer")

    def test_a_page_may_place_a_section_once(self, home_with_footer: Page, footer: Section) -> None:
        with pytest.raises(ValidationError):
            SectionPlacement.objects.create(page=home_with_footer, section=footer)


class TestMenus:
    def test_it_lists_the_menus(self, client: Client, main_menu: Menu) -> None:
        [menu] = data(client.get(MENUS))

        assert menu == {"id": "main", "name": "Main"}

    def test_a_menu_carries_its_entries_in_order(self, client: Client, main_menu: Menu) -> None:
        menu = data(client.get(f"{MENUS}/main"))

        assert [item["label"] for item in menu["items"]] == ["Home", "About"]
        assert menu["items"][1]["children"][0]["label"] == "Team"

    def test_a_page_entry_names_the_page_rather_than_its_address(
        self, client: Client, main_menu: Menu
    ) -> None:
        """So renaming a slug moves the menu with it instead of breaking it."""
        first = data(client.get(f"{MENUS}/main"))["items"][0]

        assert first["page"] == "home"
        assert first["url"] is None

    def test_labels_are_translated(self, client: Client, main_menu: Menu) -> None:
        menu = data(client.get(f"{MENUS}/main?language=fa"))

        assert menu["items"][0]["label"] == "خانه"

    def test_an_untranslated_label_falls_back(self, client: Client, main_menu: Menu) -> None:
        menu = data(client.get(f"{MENUS}/main?language=fa"))

        assert menu["items"][1]["label"] == "About"

    def test_a_hidden_entry_is_left_out(self, client: Client, main_menu: Menu) -> None:
        MenuItem.objects.filter(page__isnull=False).update(is_active=False)

        assert [item["label"] for item in data(client.get(f"{MENUS}/main"))["items"]] == ["About"]

    def test_an_unknown_menu_is_a_404(self, client: Client, main_menu: Menu) -> None:
        assert client.get(f"{MENUS}/nowhere").status_code == 404

    def test_an_entry_needs_exactly_one_destination(self, main_menu: Menu) -> None:
        with pytest.raises(ValidationError, match="Choose a page, or type a URL"):
            MenuItem.objects.create(menu=main_menu, label={"en-us": "Nowhere"})

    def test_an_entry_cannot_be_both(self, main_menu: Menu, home: Page) -> None:
        with pytest.raises(ValidationError, match="not both"):
            MenuItem.objects.create(
                menu=main_menu, label={"en-us": "Both"}, page=home, url="https://example.com"
            )

    def test_menus_nest_one_level(self, main_menu: Menu) -> None:
        child = MenuItem.objects.filter(parent__isnull=False).first()

        with pytest.raises(ValidationError, match="one level deep"):
            MenuItem.objects.create(
                menu=main_menu, parent=child, label={"en-us": "Deep"}, url="/deep"
            )


class TestMenusAndPublishing:
    """A menu is written before the page it points at goes live, every time."""

    def test_an_entry_pointing_at_a_draft_is_left_out(
        self, client: Client, main_menu: Menu, home: Page
    ) -> None:
        home.unpublish()
        home.save()

        labels = [item["label"] for item in data(client.get(f"{MENUS}/main"))["items"]]

        assert labels == ["About"]

    def test_it_comes_back_when_the_page_is_published(
        self, client: Client, main_menu: Menu, home: Page
    ) -> None:
        home.unpublish()
        home.save()
        home.publish()
        home.save()

        labels = [item["label"] for item in data(client.get(f"{MENUS}/main"))["items"]]

        assert labels == ["Home", "About"]

    def test_a_child_entry_is_dropped_too(self, client: Client, main_menu: Menu) -> None:
        draft = Page.objects.create(name="Draft", slug="draft-page")
        parent = MenuItem.objects.filter(url="https://example.com/about").first()
        MenuItem.objects.create(menu=main_menu, parent=parent, label={"en-us": "Soon"}, page=draft)

        about = data(client.get(f"{MENUS}/main"))["items"][1]

        assert [child["label"] for child in about["children"]] == ["Team"]
