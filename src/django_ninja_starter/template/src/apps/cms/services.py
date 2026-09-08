"""Reading content: what exists, what it is called, and what is on it.

Six questions, all read-only. Writing is the admin's job -- a CMS whose content
can be changed over the same API that serves it has to answer "who may edit
this?" on every request, and this one answers "nobody, here" instead.

The methods are async because they are pure reads with no work between the
query and the response, so a server that can hold connections open while the
database answers should be allowed to.

Language resolution stays with the caller. A transport knows where a preference
arrives from -- a query parameter, an ``Accept-Language`` header, a field on a
GraphQL query -- and passes the answer in; this file only obeys it.
"""

from typing import Any

from apps.cms.content import (
    menu_payload,
    menu_tree,
    page_payload,
    page_summary,
    page_tree,
    previewable_tree,
    site_payload,
)
from apps.cms.models import Menu, Page, SiteSettings
from apps.cms.preview import slug_from_token
from apps.cms.sitemap import SitemapDisabled, sitemap_xml


class ContentNotFound(LookupError):
    """No page or menu goes by that name -- or none this caller may see.

    Deliberately not distinguished from "exists but is a draft": saying which
    would turn the read API into a way to enumerate unpublished work.
    """


class SitemapNotPublished(LookupError):
    """This installation has turned its sitemap off.

    Its own class rather than :class:`ContentNotFound`, because the two mean
    different things to a caller: one is "no such page", the other is "this site
    does not do that" -- and only the second is worth an editor's attention.
    """


class ContentService:
    """Everything a reader can ask of the content an editor built."""

    async def pages(self, language: str) -> list[dict[str, Any]]:
        """Every live page, in menu order. ``id`` is the name `page` takes."""
        pages = Page.objects.live().order_by("order", "name")
        return [page_summary(page, language) async for page in pages]

    async def site(self, language: str) -> dict[str, Any]:
        """Titles, description, logo and contact details for the whole installation.

        Answers before anybody has filled the form in, with empty strings rather
        than a refusal: a client asking who it is should not have to handle the
        site not existing.
        """
        return site_payload(await SiteSettings.aload(), language)

    async def sitemap(self) -> str:
        """Every live page as sitemap XML, or a refusal if the site publishes none."""
        try:
            return await sitemap_xml()
        except SitemapDisabled as disabled:
            raise SitemapNotPublished(str(disabled)) from None

    async def menus(self) -> list[dict[str, Any]]:
        menus = Menu.objects.filter(is_active=True).order_by("name")
        return [{"id": menu.slug, "name": menu.name} async for menu in menus]

    async def menu(self, name: str, language: str) -> dict[str, Any]:
        """One menu, one level of nesting deep, with each entry's label translated."""
        try:
            menu = await menu_tree().aget(slug=name)
        except Menu.DoesNotExist:
            raise ContentNotFound("No such menu.") from None
        return menu_payload(menu, language)

    async def page(self, name: str, language: str, *, preview: str | None = None) -> dict[str, Any]:
        """The whole page: metadata, sections -- its own and shared -- and every field.

        A draft is not found unless ``preview`` carries a token that names this
        page. The token is checked against the page it was asked for, so one
        preview link does not open every unpublished page on the site.
        """
        previewing = preview is not None and slug_from_token(preview) == name
        pages = previewable_tree() if previewing else page_tree()
        try:
            page = await pages.aget(slug=name)
        except Page.DoesNotExist:
            raise ContentNotFound("No such page.") from None
        return page_payload(page, await SiteSettings.aload(), language)


content_service = ContentService()
