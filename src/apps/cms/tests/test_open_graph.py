"""What a link to a page looks like when somebody shares it.

Open Graph replaced meta keywords as the thing worth storing, and the reason it
is resolved on the server rather than in each client is here: the fallback chain
is three deep -- the page's own social copy, then its plain meta copy, then the
site's -- and four frontends working it out separately is four chances to get it
wrong. Every transport therefore answers the same already-resolved values.
"""

from typing import Any

import pytest
from django.test import Client

from apps.cms.content import page_meta, site_payload
from apps.cms.models import Page, PageStatus, SiteSettings

pytestmark = pytest.mark.django_db


@pytest.fixture
def page(db: None) -> Page:
    return Page.objects.create(
        name="Pricing",
        slug="pricing",
        status=PageStatus.PUBLISHED,
        title={"en-us": "Pricing - Acme"},
        description={"en-us": "What Acme costs."},
    )


class TestFallbacks:
    def test_a_page_that_never_set_a_social_title_shares_as_itself(
        self, page: Page, site: SiteSettings
    ) -> None:
        """Not as a blank card, and not as the home page."""
        meta = page_meta(page, site, "en-us")

        assert meta["og_title"] == site.og_title["en-us"]

    def test_with_no_site_title_either_it_falls_back_to_the_page_title(self, page: Page) -> None:
        meta = page_meta(page, SiteSettings(), "en-us")

        assert meta["og_title"] == "Pricing - Acme"

    def test_a_page_with_no_meta_title_falls_back_to_its_own_name(self) -> None:
        page = Page(name="Pricing", slug="pricing")

        assert page_meta(page, SiteSettings(), "en-us")["title"] == "Pricing"

    def test_the_page_wins_over_the_site(self, page: Page, site: SiteSettings) -> None:
        page.og_title = {"en-us": "Plans from $9"}
        page.og_image = "https://cdn.example.com/pricing-card.png"

        meta = page_meta(page, site, "en-us")

        assert meta["og_title"] == "Plans from $9"
        assert meta["og_image"] == "https://cdn.example.com/pricing-card.png"

    def test_the_social_description_falls_back_to_the_meta_one(
        self, page: Page, site: SiteSettings
    ) -> None:
        assert page_meta(page, site, "en-us")["og_description"] == "What Acme costs."

    def test_the_canonical_url_falls_back_to_the_site_s(
        self, page: Page, site: SiteSettings
    ) -> None:
        """What stops three addresses for one page being counted as three pages."""
        site.og_url = "https://acme.example.com"

        assert page_meta(page, site, "en-us")["og_url"] == "https://acme.example.com"

    def test_fallbacks_are_resolved_per_language(self, page: Page, site: SiteSettings) -> None:
        page.og_title = {"fa": "قیمت‌ها"}

        assert page_meta(page, site, "fa")["og_title"] == "قیمت‌ها"

    def test_nothing_anywhere_is_an_empty_string_rather_than_null(self) -> None:
        """A client writes it into a meta tag; `None` renders as the word None."""
        meta = page_meta(Page(name="X", slug="x"), SiteSettings(), "en-us")

        assert meta["og_description"] == ""
        assert meta["og_image"] == ""


class TestNoKeywords:
    def test_the_page_payload_carries_none(self, page: Page, site: SiteSettings) -> None:
        assert "keywords" not in page_meta(page, site, "en-us")

    def test_the_site_payload_carries_none(self, site: SiteSettings) -> None:
        assert "keywords" not in site_payload(site, "en-us")

    def test_the_model_has_no_such_column(self) -> None:
        names = {field.name for field in Page._meta.get_fields()}

        assert "keywords" not in names


class TestOverHttp:
    def test_a_page_answers_with_its_resolved_card(
        self, client: Client, page: Page, site: SiteSettings
    ) -> None:
        body = client.get("/api/v1/cms/pages/pricing?language=en-us").json()["data"]

        assert body["meta"]["og_title"] == site.og_title["en-us"]
        assert body["meta"]["og_image"] == site.og_image
        assert "keywords" not in body["meta"]

    def test_the_site_endpoint_answers_with_its_own(
        self, client: Client, site: SiteSettings
    ) -> None:
        body = client.get("/api/v1/cms/site?language=en-us").json()["data"]

        assert body["og_title"] == site.og_title["en-us"]
        assert body["og_image"] == site.og_image
        assert "keywords" not in body


class TestOverGraphQl:
    def test_the_card_is_queryable(self, client: Client, page: Page, site: SiteSettings) -> None:
        response = client.post(
            "/graphql",
            {
                "query": """
                    query { cmsPage(name: "pricing") { meta { ogTitle ogImage ogUrl } } }
                """
            },
            content_type="application/json",
        )

        meta: Any = response.json()["data"]["cmsPage"]["meta"]
        assert meta["ogTitle"] == site.og_title["en-us"]
        assert meta["ogImage"] == site.og_image
