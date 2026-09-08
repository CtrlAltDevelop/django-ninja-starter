"""Every page as schema.org JSON-LD: what a page *is*, not just what it says.

Open Graph decides what a link looks like when somebody shares it. This decides
what a search engine understands the page to be -- which is the difference
between a result that is a blue link and one that carries a site name, a
breadcrumb and a logo. The two overlap in the values they use and answer
different questions, so both are here and neither is derived from the other.

It is generated rather than typed. Asking an editor to keep a JSON-LD block up
to date is asking them to maintain a second copy of the page's title,
description and address, in a syntax where a missing brace is invisible until a
crawler silently drops the whole thing. Everything in this document is already
somewhere in the CMS; this only has to say it in the right vocabulary.

The document is a ``@graph`` of three linked nodes rather than one flat object,
because that is the shape that lets a page point at its site instead of
restating it: the ``WebPage`` ``isPartOf`` the ``WebSite``, which is
``publisher`` of the ``Organization``. Consumers follow ``@id`` references, and
a graph is how two pages are understood as two pages of one site.

Nothing here invents facts. A value nobody has filled in is left out of the
document entirely -- an empty string in a JSON-LD field is worse than a missing
one, because it is read as an assertion that the site has no name.
"""

from typing import Any

from apps.cms.models import Page, SiteSettings
from apps.cms.sitemap import base_url, page_location
from apps.cms.translations import translation


def _prune(node: dict[str, Any]) -> dict[str, Any]:
    """Drop the keys nobody filled in, keeping the ones that identify the node."""
    keep = {"@type", "@id"}
    return {key: value for key, value in node.items() if key in keep or value}


def site_graph(site: SiteSettings, language: str) -> list[dict[str, Any]]:
    """The two nodes that are true of every page: the site, and who publishes it.

    Given ``@id`` values built from the site's own address, so that a page's
    reference to them resolves rather than duplicating them. Without a
    configured address they are still valid -- the ids fall back to bare
    fragments, which are resolved against whatever document the block is
    embedded in, and that is the right answer for a site that has not been told
    its own name yet.
    """
    root = base_url(site) or ""
    name = translation(site.name, language) or ""
    return [
        _prune(
            {
                "@type": "WebSite",
                "@id": f"{root}/#website",
                "url": root,
                "name": name,
                "description": translation(site.description, language) or "",
                "inLanguage": language,
                "publisher": {"@id": f"{root}/#organization"},
            }
        ),
        _prune(
            {
                "@type": "Organization",
                "@id": f"{root}/#organization",
                "url": root,
                "name": name,
                "logo": site.logo,
                "email": site.contact.get("email", ""),
                "telephone": site.contact.get("phone", ""),
                # The profiles a search engine uses to decide that this site and
                # that account are the same organisation.
                "sameAs": [
                    link["url"] for link in site.social_links if isinstance(link.get("url"), str)
                ],
            }
        ),
    ]


def page_node(page: Page, site: SiteSettings, meta: dict[str, Any]) -> dict[str, Any]:
    """The page itself, described with the metadata the API already resolved.

    ``meta`` is what :func:`~apps.cms.content.page_meta` worked out -- title and
    description with the page's own values, then the site's, already merged.
    Recomputing that chain here would be a second implementation of it, and the
    two would disagree the first time either changed.
    """
    root = base_url(site) or ""
    location = page_location(page, site)
    return _prune(
        {
            "@type": "WebPage",
            "@id": f"{location}#webpage",
            "url": location,
            "name": meta.get("title") or page.name,
            "description": meta.get("description") or "",
            "isPartOf": {"@id": f"{root}/#website"},
            "primaryImageOfPage": meta.get("og_image") or "",
            # The publishing dates, which is how a result gets a date beside it.
            # `published_at` is the editorial one and may be in neither the past
            # nor this document -- a page live the moment it was published has
            # none -- so the row's own creation date stands in for it.
            "datePublished": (page.published_at or page.created_at).isoformat()
            if page.created_at
            else "",
            "dateModified": page.updated_at.isoformat() if page.updated_at else "",
        }
    )


def page_json_ld(page: Page, site: SiteSettings, meta: dict[str, Any], language: str) -> dict:
    """The whole block a page embeds in a ``<script type="application/ld+json">``.

    Handed over as an object rather than as a string of HTML: what to do with it
    is the frontend's decision, and a server that returns markup has decided
    that its client renders HTML.
    """
    return {
        "@context": "https://schema.org",
        "@graph": [page_node(page, site, meta), *site_graph(site, language)],
    }
