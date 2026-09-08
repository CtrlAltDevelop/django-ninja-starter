"""The three read endpoints, and the language each caller ends up with."""

from typing import Any

import pytest
from django.test import Client

from apps.cms.models import Field, Page, Section, SiteSettings

pytestmark = pytest.mark.django_db

PAGES = "/api/v1/cms/pages"
SITE = "/api/v1/cms/site"


def data(response: Any) -> Any:
    assert response.status_code == 200, response.content
    return response.json()["data"]


class TestPageList:
    def test_it_lists_the_name_to_show_and_the_name_to_ask_for(
        self, client: Client, home: Page
    ) -> None:
        [page] = data(client.get(PAGES))

        assert page["id"] == "home"
        assert page["name"] == "Home"

    def test_a_draft_is_not_listed(self, client: Client, home: Page, draft: Page) -> None:
        listed = [page["id"] for page in data(client.get(PAGES))]

        assert listed == ["home"]

    def test_a_page_dated_for_later_is_not_listed_yet(
        self, client: Client, home: Page, scheduled: Page
    ) -> None:
        listed = [page["id"] for page in data(client.get(PAGES))]

        assert listed == ["home"]

    def test_pages_come_back_in_menu_order(self, client: Client, home: Page) -> None:
        Page.objects.create(name="About", slug="about", order=5, status="published")
        Page.objects.filter(pk=home.pk).update(order=9)

        assert [page["id"] for page in data(client.get(PAGES))] == ["about", "home"]


class TestSite:
    def test_it_answers_before_anybody_has_filled_the_form_in(self, client: Client) -> None:
        payload = data(client.get(SITE))

        assert payload["name"] == ""
        assert payload["languages"] == ["en-us", "fa"]

    def test_it_returns_the_requested_language(self, client: Client, site: SiteSettings) -> None:
        payload = data(client.get(f"{SITE}?language=fa"))

        assert payload["name"] == "آکمی"
        assert payload["language"] == "fa"

    def test_an_untranslated_line_falls_back(self, client: Client, site: SiteSettings) -> None:
        assert data(client.get(f"{SITE}?language=fa"))["tagline"] == "We make things"


class TestPageDetail:
    def test_it_returns_the_sections_in_order_with_their_fields(
        self, client: Client, home: Page
    ) -> None:
        payload = data(client.get(f"{PAGES}/home"))

        assert [section["id"] for section in payload["sections"]] == ["hero", "plans"]
        hero, plans = payload["sections"]
        assert [field["id"] for field in hero["fields"]] == ["headline", "background"]
        assert hero["fields"][0]["value"] == "Welcome"
        assert plans["children"][0]["fields"][0]["value"] == 9

    def test_every_field_says_what_type_its_value_has(self, client: Client, home: Page) -> None:
        payload = data(client.get(f"{PAGES}/home"))
        background = payload["sections"][0]["fields"][1]

        assert background["type"] == "image"
        assert background["value"]["url"] == "https://cdn.example.com/hero.jpg"

    def test_page_metadata_falls_back_to_the_site(
        self, client: Client, home: Page, site: SiteSettings
    ) -> None:
        meta = data(client.get(f"{PAGES}/home"))["meta"]

        assert meta["title"] == "Acme - Home"
        assert meta["description"] == "The Acme site"

    def test_an_inactive_section_is_left_out(self, client: Client, home: Page) -> None:
        Section.objects.filter(slug="plans").update(is_active=False)

        assert [s["id"] for s in data(client.get(f"{PAGES}/home"))["sections"]] == ["hero"]

    def test_an_inactive_field_is_left_out(self, client: Client, home: Page) -> None:
        Field.objects.filter(slug="background").update(is_active=False)

        hero = data(client.get(f"{PAGES}/home"))["sections"][0]
        assert [field["id"] for field in hero["fields"]] == ["headline"]

    def test_an_unknown_page_is_a_404(self, client: Client, home: Page) -> None:
        response = client.get(f"{PAGES}/nowhere")

        assert response.status_code == 404
        assert response.json()["title"] == "NOT_FOUND"

    def test_a_draft_cannot_be_read(self, client: Client, draft: Page) -> None:
        assert client.get(f"{PAGES}/secret").status_code == 404


class TestLanguageChoice:
    def test_the_browsers_preference_is_honoured(self, client: Client, home: Page) -> None:
        payload = data(client.get(f"{PAGES}/home", headers={"accept-language": "fa,en;q=0.8"}))

        assert payload["language"] == "fa"
        assert payload["sections"][0]["fields"][0]["value"] == "خوش آمدید"

    def test_an_explicit_language_beats_the_header(self, client: Client, home: Page) -> None:
        payload = data(
            client.get(f"{PAGES}/home?language=en-us", headers={"accept-language": "fa"})
        )

        assert payload["language"] == "en-us"

    def test_a_regional_variant_matches_its_language(self, client: Client, home: Page) -> None:
        assert data(client.get(f"{PAGES}/home?language=en-GB"))["language"] == "en-us"

    def test_a_language_nobody_writes_gets_the_default(self, client: Client, home: Page) -> None:
        payload = data(client.get(f"{PAGES}/home", headers={"accept-language": "de-DE"}))

        assert payload["language"] == "en-us"

    def test_the_page_is_one_query_tree_however_many_sections(
        self, client: Client, home: Page, django_assert_num_queries: Any
    ) -> None:
        """Adding sections must not add queries: that is what the prefetch buys.

        Seven, whatever the page holds: the page, its own sections, their
        fields, the child sections, their fields, the shared placements, and
        the site row the metadata falls back to.
        """
        for index in range(5):
            section = Section.objects.create(page=home, name=f"S{index}", slug=f"s{index}")
            Field.objects.create(section=section, name="T", slug="t", values={"en-us": "x"})

        with django_assert_num_queries(7):
            client.get(f"{PAGES}/home")
