"""The JSON-LD every page carries: what it claims, and what it refuses to claim.

Two properties matter more than the exact vocabulary. The block is *linked* --
the page points at the site rather than restating it -- and it is *honest*: a
value nobody filled in is missing rather than empty, because an empty name in
JSON-LD is an assertion that the site has none.
"""

from typing import Any

import pytest
from django.test import Client

from apps.cms.content import page_meta
from apps.cms.models import Page, SiteSettings
from apps.cms.structured_data import page_json_ld

pytestmark = pytest.mark.django_db

PAGES = "/api/v1/cms/pages"


def block(page: Page, site: SiteSettings, language: str = "en-us") -> dict[str, Any]:
    return page_json_ld(page, site, page_meta(page, site, language), language)


def node(document: dict[str, Any], kind: str) -> dict[str, Any]:
    return next(item for item in document["@graph"] if item["@type"] == kind)


class TestTheGraph:
    def test_it_describes_the_page_the_site_and_the_publisher(
        self, home: Page, site: SiteSettings
    ) -> None:
        types = {item["@type"] for item in block(home, site)["@graph"]}

        assert types == {"WebPage", "WebSite", "Organization"}

    def test_the_page_points_at_the_site_rather_than_restating_it(
        self, home: Page, site: SiteSettings
    ) -> None:
        document = block(home, site)

        assert node(document, "WebPage")["isPartOf"]["@id"] == node(document, "WebSite")["@id"]

    def test_the_site_names_its_publisher(self, home: Page, site: SiteSettings) -> None:
        document = block(home, site)

        assert (
            node(document, "WebSite")["publisher"]["@id"] == node(document, "Organization")["@id"]
        )

    def test_it_declares_the_schema_org_context(self, home: Page, site: SiteSettings) -> None:
        assert block(home, site)["@context"] == "https://schema.org"


class TestWhatItSays:
    def test_the_page_carries_the_resolved_title(self, home: Page, site: SiteSettings) -> None:
        assert node(block(home, site), "WebPage")["name"] == "Acme - Home"

    def test_it_answers_in_the_language_that_was_asked_for(
        self, home: Page, site: SiteSettings
    ) -> None:
        document = block(home, site, "fa")

        assert node(document, "WebSite")["name"] == "آکمی"
        assert node(document, "WebSite")["inLanguage"] == "fa"

    def test_social_links_become_the_publishers_other_profiles(
        self, home: Page, site: SiteSettings
    ) -> None:
        assert node(block(home, site), "Organization")["sameAs"] == ["https://x.example.com/acme"]

    def test_a_value_nobody_wrote_is_left_out_rather_than_left_empty(self, home: Page) -> None:
        """An empty string in JSON-LD is a claim. A missing key is not."""
        organization = node(block(home, SiteSettings()), "Organization")

        assert "logo" not in organization
        assert "email" not in organization

    def test_the_dates_are_the_pages_own(self, home: Page, site: SiteSettings) -> None:
        page = node(block(home, site), "WebPage")

        assert page["dateModified"] == home.updated_at.isoformat()


class TestOverTheApi:
    def test_every_page_is_served_with_its_block(
        self, client: Client, home: Page, site: SiteSettings
    ) -> None:
        payload = client.get(f"{PAGES}/home").json()["data"]

        assert payload["json_ld"]["@context"] == "https://schema.org"
        assert {item["@type"] for item in payload["json_ld"]["@graph"]} == {
            "WebPage",
            "WebSite",
            "Organization",
        }

    def test_it_uses_the_language_the_reader_asked_for(
        self, client: Client, home: Page, site: SiteSettings
    ) -> None:
        payload = client.get(f"{PAGES}/home?language=fa").json()["data"]
        website = next(item for item in payload["json_ld"]["@graph"] if item["@type"] == "WebSite")

        assert website["inLanguage"] == "fa"
