"""GraphQL types for the content an editor built.

``value`` is a ``JSON`` scalar and that is the point: a field's shape is decided
by its ``type``, which the same object carries, so a client switches on the type
and knows exactly what it has. A union of every canonical shape would have to be
edited every time an editor invents a use for the ``meta`` object.
"""

from typing import Any

import strawberry
from strawberry.scalars import JSON


@strawberry.type
class FieldType:
    """One piece of content, already resolved to the requested language."""

    id: str
    name: str
    type: str
    multiple: bool
    required: bool
    value: JSON | None


@strawberry.type
class SectionType:
    id: str
    name: str
    shared: bool
    fields: list[FieldType]
    children: list["SectionType"]


@strawberry.type
class PageMetaType:
    title: str
    description: str | None
    og_title: str
    og_description: str
    og_image: str
    og_url: str


@strawberry.type
class PageSummaryType:
    """A row in a menu: ``id`` is what the page field is asked for."""

    id: str
    name: str
    title: str
    order: int
    updated_at: str


@strawberry.type
class PageType:
    id: str
    name: str
    language: str
    status: str
    meta: PageMetaType
    sections: list[SectionType]


@strawberry.type
class MenuSummaryType:
    id: str
    name: str


@strawberry.type
class MenuItemType:
    """One entry. ``page`` names a page in this CMS; ``url`` is anything else."""

    label: str
    page: str | None
    url: str | None
    new_tab: bool
    children: list["MenuItemType"]


@strawberry.type
class MenuType:
    id: str
    name: str
    items: list[MenuItemType]


@strawberry.type
class SiteType:
    """Everything that is true of the whole site rather than of one page."""

    language: str
    languages: list[str]
    default_language: str
    name: str
    tagline: str
    description: str
    og_title: str
    og_description: str
    logo: str
    favicon: str
    og_image: str
    og_url: str
    contact: JSON
    social_links: JSON
    extra: JSON


def field_type(field: dict[str, Any]) -> FieldType:
    return FieldType(
        id=field["id"],
        name=field["name"],
        type=str(field["type"]),
        multiple=field["multiple"],
        required=field["required"],
        value=field.get("value"),
    )


def section_type(section: dict[str, Any]) -> SectionType:
    return SectionType(
        id=section["id"],
        name=section["name"],
        shared=section.get("shared", False),
        fields=[field_type(field) for field in section.get("fields", [])],
        children=[section_type(child) for child in section.get("children", [])],
    )


def page_summary_type(page: dict[str, Any]) -> PageSummaryType:
    return PageSummaryType(
        id=page["id"],
        name=page["name"],
        title=page["title"],
        order=page["order"],
        updated_at=page["updated_at"].isoformat(),
    )


def page_type(page: dict[str, Any]) -> PageType:
    meta = page["meta"]
    return PageType(
        id=page["id"],
        name=page["name"],
        language=page["language"],
        status=page["status"],
        meta=PageMetaType(
            title=meta["title"],
            description=meta.get("description"),
            og_title=meta.get("og_title", ""),
            og_description=meta.get("og_description", ""),
            og_image=meta.get("og_image", ""),
            og_url=meta.get("og_url", ""),
        ),
        sections=[section_type(section) for section in page.get("sections", [])],
    )


def menu_item_type(item: dict[str, Any]) -> MenuItemType:
    return MenuItemType(
        label=item["label"],
        page=item.get("page"),
        url=item.get("url"),
        new_tab=item.get("new_tab", False),
        children=[menu_item_type(child) for child in item.get("children", [])],
    )


def menu_type(menu: dict[str, Any]) -> MenuType:
    return MenuType(
        id=menu["id"],
        name=menu["name"],
        items=[menu_item_type(item) for item in menu.get("items", [])],
    )


def site_type(site: dict[str, Any]) -> SiteType:
    return SiteType(
        language=site["language"],
        languages=site["languages"],
        default_language=site["default_language"],
        name=site["name"],
        tagline=site["tagline"],
        description=site["description"],
        og_title=site.get("og_title", ""),
        og_description=site.get("og_description", ""),
        logo=site.get("logo", ""),
        favicon=site.get("favicon", ""),
        og_image=site.get("og_image", ""),
        og_url=site.get("og_url", ""),
        contact=site.get("contact", {}),
        social_links=site.get("social_links", []),
        extra=site.get("extra", {}),
    )
