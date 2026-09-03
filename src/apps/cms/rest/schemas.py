"""The contract the CMS endpoints publish.

``value`` is typed ``Any`` and that is the point: a field's shape is decided by
its ``type``, which the same object carries, so a client switches on the type
and knows exactly what it has. Pinning it to a union of every canonical shape
would make the OpenAPI document longer, the generated clients worse, and would
have to be edited every time an editor invents a use for the ``meta`` object.
"""

from datetime import datetime
from typing import Any

from ninja import Schema

from apps.cms.fields import FieldType


class MessageOut(Schema):
    detail: str


class FieldOut(Schema):
    """One piece of content, already resolved to the requested language."""

    id: str
    name: str
    type: FieldType
    multiple: bool
    required: bool
    value: Any = None


class SectionOut(Schema):
    id: str
    name: str
    shared: bool = False
    fields: list[FieldOut] = []
    children: list["SectionOut"] = []


class PageSummaryOut(Schema):
    """A row in a menu: ``id`` is what the detail endpoint is asked for."""

    id: str
    name: str
    title: str
    order: int
    updated_at: datetime


class PageMetaOut(Schema):
    """A page's metadata, with the site's already merged in behind it.

    The four ``og_`` values are what a link to this page looks like when it is
    shared, and they are resolved rather than raw: blank ones have already
    fallen back to the page's plainer wording and then to the site's, so a
    client renders them straight out without a fallback chain of its own.
    """

    title: str
    description: str | None = None
    og_title: str = ""
    og_description: str = ""
    og_image: str = ""
    og_url: str = ""


class PageOut(Schema):
    id: str
    name: str
    language: str
    status: str
    meta: PageMetaOut
    sections: list[SectionOut] = []


class MenuSummaryOut(Schema):
    id: str
    name: str


class MenuItemOut(Schema):
    """One entry. ``page`` names a page in this CMS; ``url`` is anything else."""

    label: str
    page: str | None = None
    url: str | None = None
    new_tab: bool = False
    children: list["MenuItemOut"] = []


class MenuOut(Schema):
    id: str
    name: str
    items: list[MenuItemOut] = []


class SiteOut(Schema):
    """Everything that is true of the whole site rather than of one page."""

    language: str
    languages: list[str]
    default_language: str
    name: str
    tagline: str
    description: str
    og_title: str = ""
    og_description: str = ""
    logo: str = ""
    favicon: str = ""
    og_image: str = ""
    og_url: str = ""
    contact: dict[str, str] = {}
    social_links: list[dict[str, Any]] = []
    extra: dict[str, Any] = {}


SectionOut.model_rebuild()
MenuItemOut.model_rebuild()
