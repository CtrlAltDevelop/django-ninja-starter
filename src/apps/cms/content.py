"""Turn stored rows into the JSON one language's reader gets.

Kept apart from the models because the two answer different questions. A model
says what may be stored; this says what a client is shown -- drafts gone, hidden
rows gone, one language chosen, page metadata already merged over the site's
defaults, and the page's own sections interleaved with the library sections
placed on it. A client should not have to know which of those a band of content
came from, and it certainly should not have to make a second request to find out.

The querysets below are the other half of that. Rendering a page touches every
section, every placement and every field on it, which is the classic shape of an
N+1: fetch the page, then a query per section, then a query per field.
Prefetching the whole tree up front makes it a fixed handful whatever the page
contains.
"""

from typing import Any

from django.db.models import Prefetch, Q, QuerySet

from apps.cms.models import Field, Menu, MenuItem, Page, Section, SectionPlacement, SiteSettings
from apps.cms.translations import default_language, known_languages, translation


def _active_fields() -> Prefetch:
    return Prefetch(
        "fields", queryset=Field.objects.filter(is_active=True).order_by("order", "name")
    )


def _section_tree(queryset: QuerySet[Section]) -> QuerySet[Section]:
    """A section queryset with its children and everybody's fields attached."""
    children = Prefetch(
        "children",
        queryset=Section.objects.filter(is_active=True)
        .order_by("order", "name")
        .prefetch_related(_active_fields()),
    )
    return queryset.prefetch_related(_active_fields(), children)


def _with_tree(pages: QuerySet[Page]) -> QuerySet[Page]:
    """Attach a page queryset's whole visible tree in a fixed number of queries."""
    sections = Prefetch(
        "sections",
        queryset=_section_tree(
            Section.objects.filter(is_active=True, parent__isnull=True).order_by("order", "name")
        ),
    )
    placements = Prefetch(
        "placements",
        queryset=SectionPlacement.objects.filter(is_active=True, section__is_active=True)
        .order_by("order")
        # Prefetched rather than joined: the section carries its own children
        # and fields, and a field cannot be both selected and prefetched.
        .prefetch_related(Prefetch("section", queryset=_section_tree(Section.objects.all()))),
    )
    return pages.prefetch_related(sections, placements)


def page_tree() -> QuerySet[Page]:
    """Live pages with their whole visible tree already loaded.

    "Live" is the API's whole publishing rule in one place: a draft, or a page
    dated for next Tuesday, is not here.
    """
    return _with_tree(Page.objects.live())


def previewable_tree() -> QuerySet[Page]:
    """The same tree for any page, reached only with a signed preview link."""
    return _with_tree(Page.objects.all())


def _linkable() -> Q:
    """An entry a reader can actually follow.

    An entry pointing at a page nobody has published yet would be a link to a
    404 -- which is what happens when the menu is written before the page goes
    live, and it is written before the page goes live almost every time.
    """
    return Q(page__isnull=True) | Q(page__in=Page.objects.live().values("pk"))


def menu_tree() -> QuerySet[Menu]:
    """Active menus with the entries a reader can follow, one level deep."""
    children = Prefetch(
        "children",
        queryset=MenuItem.objects.filter(_linkable(), is_active=True)
        .select_related("page")
        .order_by("order"),
    )
    items = Prefetch(
        "items",
        queryset=MenuItem.objects.filter(_linkable(), is_active=True, parent__isnull=True)
        .select_related("page")
        .order_by("order")
        .prefetch_related(children),
    )
    return Menu.objects.filter(is_active=True).prefetch_related(items)


def field_payload(field: Field, language: str) -> dict[str, Any]:
    return {
        "id": field.slug,
        "name": field.name,
        "type": field.field_type,
        "multiple": field.multiple,
        "required": field.required,
        "value": field.value_for(language),
    }


def section_payload(
    section: Section, language: str, *, nested: bool = False, shared: bool = False
) -> dict[str, Any]:
    """One section and its children, reading only what was prefetched.

    ``nested`` is what keeps that promise. Nesting stops at one level, so a
    child has no children to ask about -- and asking anyway would be a query per
    child, issued from an async view, which is an error rather than merely slow.

    ``shared`` tells a client that this band is the library's rather than the
    page's. Rendering does not change; a "this appears on nine pages" hint in an
    editing UI does.
    """
    return {
        "id": section.slug,
        "name": section.name,
        "shared": shared,
        "fields": [field_payload(field, language) for field in section.fields.all()],
        "children": []
        if nested
        else [section_payload(child, language, nested=True) for child in section.children.all()],
    }


def page_sections(page: Page, language: str) -> list[dict[str, Any]]:
    """The page's own sections and its placed library sections, in one order.

    Merged here rather than left to the client, because "footer last" is a fact
    about the page and not about which table the row came from.
    """
    ordered: list[tuple[int, str, dict[str, Any]]] = [
        (section.order, section.name, section_payload(section, language))
        for section in page.sections.all()
    ]
    ordered.extend(
        (
            placement.order,
            placement.section.name,
            section_payload(placement.section, language, shared=True),
        )
        for placement in page.placements.all()
    )
    return [payload for _, _, payload in sorted(ordered, key=lambda row: (row[0], row[1]))]


def page_meta(page: Page, site: SiteSettings, language: str) -> dict[str, Any]:
    """The page's own metadata, with the site's used for whatever it omits."""
    return {
        "title": translation(page.title, language) or page.name,
        "description": translation(page.description, language)
        or translation(site.description, language),
        "keywords": translation(page.keywords, language)
        or translation(site.keywords, language)
        or [],
        "og_image": page.og_image or site.og_image,
    }


def page_summary(page: Page, language: str) -> dict[str, Any]:
    """Enough to build a menu: the name to show and the name to ask for."""
    return {
        "id": page.slug,
        "name": page.name,
        "title": translation(page.title, language) or page.name,
        "order": page.order,
        "updated_at": page.updated_at,
    }


def page_payload(page: Page, site: SiteSettings, language: str) -> dict[str, Any]:
    return {
        "id": page.slug,
        "name": page.name,
        "language": language,
        "status": page.status,
        "meta": page_meta(page, site, language),
        "sections": page_sections(page, language),
    }


def menu_item_payload(item: MenuItem, language: str, *, nested: bool = False) -> dict[str, Any]:
    """One entry, with the page it points at named rather than addressed.

    A client routes its own pages, so it is given the page's ``id`` and left to
    build the URL. An entry pointing anywhere else carries that address as it
    was typed.
    """
    return {
        "label": translation(item.label, language) or "",
        "page": item.page.slug if item.page_id else None,
        "url": item.url or None,
        "new_tab": item.new_tab,
        "children": []
        if nested
        else [menu_item_payload(child, language, nested=True) for child in item.children.all()],
    }


def menu_payload(menu: Menu, language: str) -> dict[str, Any]:
    return {
        "id": menu.slug,
        "name": menu.name,
        "items": [menu_item_payload(item, language) for item in menu.items.all()],
    }


def site_payload(site: SiteSettings, language: str) -> dict[str, Any]:
    return {
        "language": language,
        "languages": known_languages(),
        "default_language": default_language(),
        "name": translation(site.name, language) or "",
        "tagline": translation(site.tagline, language) or "",
        "description": translation(site.description, language) or "",
        "keywords": translation(site.keywords, language) or [],
        "logo": site.logo,
        "favicon": site.favicon,
        "og_image": site.og_image,
        "contact": site.contact,
        "social_links": site.social_links,
        "extra": site.extra,
    }
