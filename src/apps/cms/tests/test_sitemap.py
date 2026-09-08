"""The generated sitemap: what is in it, what is left out, and where it points.

The rule worth protecting is that the sitemap cannot disagree with the site. A
draft, a page dated for later and a page an editor excluded are absent for the
same reasons they are absent from the read API, and none of the three is a
separate implementation of "live".
"""

from decimal import Decimal

import pytest
from django.test import Client

from apps.cms.models import Page, PageStatus, SiteSettings
from apps.cms.sitemap import base_url, entry_for, page_location, render

pytestmark = pytest.mark.django_db

SITEMAP = "/api/v1/cms/sitemap.xml"


class TestDocument:
    def test_it_lists_a_live_page(self, client: Client, home: Page) -> None:
        response = client.get(SITEMAP)

        assert response.status_code == 200
        assert response["Content-Type"] == "application/xml"
        assert "<loc>/home</loc>" in response.content.decode()

    def test_it_is_xml_rather_than_the_json_envelope(self, client: Client, home: Page) -> None:
        """Crawlers read sitemaps, and no crawler unwraps this API's envelope."""
        body = client.get(SITEMAP).content.decode()

        assert body.startswith('<?xml version="1.0" encoding="UTF-8"?>')
        assert "isSuccess" not in body

    def test_a_draft_is_not_listed(self, client: Client, home: Page, draft: Page) -> None:
        assert "secret" not in client.get(SITEMAP).content.decode()

    def test_a_page_dated_for_later_is_not_listed_yet(
        self, client: Client, home: Page, scheduled: Page
    ) -> None:
        assert "launch" not in client.get(SITEMAP).content.decode()

    def test_a_page_can_be_left_out(self, client: Client, home: Page) -> None:
        Page.objects.filter(pk=home.pk).update(in_sitemap=False)

        assert "<loc>" not in client.get(SITEMAP).content.decode()

    def test_a_site_with_nothing_published_still_renders_a_document(self, client: Client) -> None:
        body = client.get(SITEMAP).content.decode()

        assert "<urlset" in body
        assert "<url>" not in body


class TestAddresses:
    def test_the_base_url_makes_entries_absolute(
        self, client: Client, home: Page, site: SiteSettings
    ) -> None:
        SiteSettings.objects.filter(pk=site.pk).update(sitemap_base_url="https://acme.test/")

        assert "<loc>https://acme.test/home</loc>" in client.get(SITEMAP).content.decode()

    def test_it_falls_back_to_the_canonical_url(self, site: SiteSettings) -> None:
        site.og_url = "https://acme.test"
        site.save()

        assert base_url(site) == "https://acme.test"

    def test_a_page_with_its_own_canonical_url_is_listed_at_that_address(
        self, home: Page, site: SiteSettings
    ) -> None:
        home.og_url = "https://elsewhere.test/welcome"
        home.save()

        assert page_location(home, site) == "https://elsewhere.test/welcome"

    def test_a_base_url_with_a_path_keeps_it(self, site: SiteSettings) -> None:
        """A site served under a prefix is a site, and its pages hang off the prefix."""
        page = Page.objects.create(name="About", slug="about", status=PageStatus.PUBLISHED)
        site.sitemap_base_url = "https://acme.test/site"
        site.save()

        assert page_location(page, site) == "https://acme.test/site/about"


class TestDefaults:
    def test_a_page_falls_back_to_the_sites_frequency_and_priority(
        self, home: Page, site: SiteSettings
    ) -> None:
        site.sitemap_changefreq = "daily"
        site.sitemap_priority = Decimal("0.7")
        site.save()

        entry = entry_for(home, site)

        assert entry.change_frequency == "daily"
        assert entry.priority == Decimal("0.7")

    def test_a_page_overrides_both(self, home: Page, site: SiteSettings) -> None:
        home.sitemap_changefreq = "hourly"
        home.sitemap_priority = Decimal("1.0")
        home.save()

        entry = entry_for(home, site)

        assert entry.change_frequency == "hourly"
        assert "<priority>1.0</priority>" in render([entry])

    def test_the_last_modified_date_is_the_pages_own(self, home: Page, site: SiteSettings) -> None:
        assert f"<lastmod>{home.updated_at.date().isoformat()}</lastmod>" in render(
            [entry_for(home, site)]
        )


class TestSwitchedOff:
    def test_a_site_that_publishes_none_answers_404(
        self, client: Client, home: Page, site: SiteSettings
    ) -> None:
        """A 404 rather than an empty document, which would claim there are no pages."""
        SiteSettings.objects.filter(pk=site.pk).update(sitemap_enabled=False)

        response = client.get(SITEMAP)

        assert response.status_code == 404
        assert response.json()["title"] == "NOT_FOUND"
