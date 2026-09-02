"""Reading content: what exists, what it is called, and what is on it.

Five endpoints, all public and all read-only. Writing is the admin's job -- a
CMS whose content can be changed over the same API that serves it has to answer
"who may edit this?" on every request, and this one answers "nobody, here"
instead.

They are async because they are pure reads with no work between the query and
the response, so a server that can hold connections open while the database
answers should be allowed to.

The language comes from ``?language=``, then ``Accept-Language``, then the
configured default. A client with a language switcher passes the parameter and
is obeyed; a client with none is served the reader's browser preference.

Nothing here imports the project around it. Refusals are raised as Django
Ninja's own ``HttpError``, which any project renders -- including this one,
whose envelope turns a 404 into ``{"title": "NOT_FOUND", ...}`` without this app
knowing that it will.
"""

from django.http import HttpRequest
from ninja import Router
from ninja.errors import HttpError

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
from apps.cms.schemas import MenuOut, MenuSummaryOut, PageOut, PageSummaryOut, SiteOut
from apps.cms.translations import resolve_language

router = Router()


@router.get("/pages", response=list[PageSummaryOut], summary="List every published page")
async def list_pages(request: HttpRequest, language: str | None = None) -> list[dict]:
    """Every live page, in menu order. ``id`` is the name the detail route takes."""
    chosen = resolve_language(request, language)
    pages = Page.objects.live().order_by("order", "name")
    return [page_summary(page, chosen) async for page in pages]


@router.get("/site", response=SiteOut, summary="Read the site metadata")
async def read_site(request: HttpRequest, language: str | None = None) -> dict:
    """Titles, description, logo and contact details for the whole installation.

    Answers before anybody has filled the form in, with empty strings rather
    than a 404: a client asking who it is should not have to handle the site
    not existing.
    """
    chosen = resolve_language(request, language)
    return site_payload(await SiteSettings.aload(), chosen)


@router.get("/menus", response=list[MenuSummaryOut], summary="List every menu")
async def list_menus(request: HttpRequest) -> list[dict]:
    menus = Menu.objects.filter(is_active=True).order_by("name")
    return [{"id": menu.slug, "name": menu.name} async for menu in menus]


@router.get("/menus/{menu_name}", response=MenuOut, summary="Read one menu")
async def read_menu(request: HttpRequest, menu_name: str, language: str | None = None) -> dict:
    """One menu, one level of nesting deep, with each entry's label translated."""
    chosen = resolve_language(request, language)
    try:
        menu = await menu_tree().aget(slug=menu_name)
    except Menu.DoesNotExist:
        raise HttpError(404, "No such menu.") from None
    return menu_payload(menu, chosen)


@router.get(
    "/pages/{page_name}",
    response=PageOut,
    summary="Read one page and everything on it",
)
async def read_page(
    request: HttpRequest,
    page_name: str,
    language: str | None = None,
    preview: str | None = None,
) -> dict:
    """The whole page: metadata, sections -- its own and shared -- and every field.

    A draft is a 404 unless ``?preview=`` carries a token that names this page.
    The token is checked against the page it was asked for, so one preview link
    does not open every unpublished page on the site.
    """
    chosen = resolve_language(request, language)
    previewing = preview is not None and slug_from_token(preview) == page_name
    pages = previewable_tree() if previewing else page_tree()
    try:
        page = await pages.aget(slug=page_name)
    except Page.DoesNotExist:
        raise HttpError(404, "No such page.") from None
    return page_payload(page, await SiteSettings.aload(), chosen)
