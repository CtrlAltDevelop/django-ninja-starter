"""The sitemap: every live page, as the XML a crawler expects.

Generated rather than written, because a sitemap is the one SEO artefact that is
wrong the moment anybody publishes anything. A hand-kept ``sitemap.xml`` lists
what was live when somebody last remembered it; this lists what is live when it
is asked for, using exactly the publishing rule the read API uses -- so a draft
and a page dated for next Tuesday are absent for the same reason they are absent
everywhere else, and there is no second definition of "live" to drift.

What an editor controls is the handful of judgements a generator cannot make:
whether to publish one at all, what address the pages actually hang off, and how
often a crawler should come back. Those live on the site settings row and, per
page, on the page -- so a thank-you page that is live and has no business in a
search index is excluded by the person who knows that, without a deployment.

The XML is built by hand rather than through Django's ``sitemaps`` framework.
That framework wants a ``Site`` row, a template loader and a URLconf entry, and
this app is meant to be lifted into a project that has none of those: the
document is nine tags, and owning them costs less than the dependency.
"""

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any
from urllib.parse import quote, urlsplit, urlunsplit
from xml.sax.saxutils import escape

from django.db.models import QuerySet

from apps.cms.models import Page, SiteSettings

#: The schema every consumer of a sitemap agrees on.
NAMESPACE = "http://www.sitemaps.org/schemas/sitemap/0.9"
CONTENT_TYPE = "application/xml"


class SitemapDisabled(LookupError):
    """This installation publishes no sitemap, and says so with a 404.

    A 404 rather than an empty document: an empty sitemap tells a crawler the
    site has no pages, which is a claim, where a missing one tells it there is
    nothing to read here, which is the truth.
    """


@dataclass(frozen=True, slots=True)
class SitemapEntry:
    """One ``<url>``: where it is, when it changed, and how to treat it."""

    location: str
    last_modified: datetime | None
    change_frequency: str
    priority: Decimal

    def as_xml(self) -> str:
        rows = [f"    <loc>{escape(self.location)}</loc>"]
        if self.last_modified is not None:
            # W3C datetime, which is what the schema asks for -- and what makes
            # "has this changed since I last looked" answerable without a fetch.
            rows.append(f"    <lastmod>{self.last_modified.date().isoformat()}</lastmod>")
        rows.append(f"    <changefreq>{self.change_frequency}</changefreq>")
        rows.append(f"    <priority>{self.priority:.1f}</priority>")
        body = "\n".join(rows)
        return f"  <url>\n{body}\n  </url>"


def base_url(site: SiteSettings) -> str:
    """The address pages hang off, with any trailing slash taken back off.

    Falls back to the canonical URL, because a site that has filled that in has
    already answered this question and should not be asked it twice.
    """
    return (site.sitemap_base_url or site.og_url or "").rstrip("/")


def page_location(page: Page, site: SiteSettings) -> str:
    """Where this page really lives.

    A page carrying its own canonical URL is served at that address, whatever
    this site is called -- which is the case a sitemap listing the wrong one of
    two addresses is built to avoid.
    """
    if page.og_url:
        return page.og_url
    base = base_url(site)
    # Quoted rather than trusted: a slug is validated on the way in, and a
    # sitemap that is only well-formed because some other field's validator held
    # is a sitemap one loosened validator breaks.
    path = quote(f"/{page.slug}")
    if not base:
        return path
    split = urlsplit(base)
    return urlunsplit((split.scheme, split.netloc, f"{split.path}{path}", "", ""))


def entry_for(page: Page, site: SiteSettings) -> SitemapEntry:
    """One page's row, with the site's defaults behind whatever it left blank."""
    return SitemapEntry(
        location=page_location(page, site),
        last_modified=page.updated_at,
        change_frequency=page.sitemap_changefreq or site.sitemap_changefreq,
        priority=(
            page.sitemap_priority if page.sitemap_priority is not None else site.sitemap_priority
        ),
    )


def listed_pages() -> QuerySet[Page]:
    """The pages that belong in a sitemap: live, and not opted out."""
    return Page.objects.live().filter(in_sitemap=True).order_by("order", "name")


def render(entries: Iterable[SitemapEntry]) -> str:
    """The whole document, ready to be sent as ``application/xml``.

    A site with nothing published renders the empty ``<urlset>`` rather than
    refusing: it is a true statement about a site that has not launched, and it
    is what a crawler that has been given this address already expects to read.
    """
    rows = [entry.as_xml() for entry in entries]
    lines = ['<?xml version="1.0" encoding="UTF-8"?>', f'<urlset xmlns="{NAMESPACE}">']
    lines.extend(rows)
    lines.append("</urlset>")
    return "\n".join(lines) + "\n"


async def sitemap_xml() -> str:
    """The sitemap this installation publishes, or a refusal if it publishes none."""
    site = await SiteSettings.aload()
    if not site.sitemap_enabled:
        raise SitemapDisabled("This site publishes no sitemap.")
    return render([entry_for(page, site) async for page in listed_pages()])


def sitemap_summary(site: SiteSettings | None = None) -> dict[str, Any]:
    """What the admin shows about the sitemap: is it on, where, how many pages."""
    site = site or SiteSettings.load()
    return {
        "enabled": site.sitemap_enabled,
        "base_url": base_url(site),
        "pages": listed_pages().count(),
        "excluded": Page.objects.live().filter(in_sitemap=False).count(),
    }
