"""Reading content over HTTP: five endpoints, all public and all read-only.

Every decision is :class:`ContentService`'s. What is decided here is what only
HTTP can decide: where the language preference comes from, and that a name
nobody published is a 404.

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

from apps.cms.rest.schemas import MenuOut, MenuSummaryOut, PageOut, PageSummaryOut, SiteOut
from apps.cms.services import ContentNotFound, content_service
from apps.cms.translations import resolve_language

router = Router()


@router.get("/pages", response=list[PageSummaryOut], summary="List every published page")
async def list_pages(request: HttpRequest, language: str | None = None) -> list[dict]:
    """Every live page, in menu order. ``id`` is the name the detail route takes."""
    return await content_service.pages(resolve_language(request, language))


@router.get("/site", response=SiteOut, summary="Read the site metadata")
async def read_site(request: HttpRequest, language: str | None = None) -> dict:
    """Titles, description, logo and contact details for the whole installation."""
    return await content_service.site(resolve_language(request, language))


@router.get("/menus", response=list[MenuSummaryOut], summary="List every menu")
async def list_menus(request: HttpRequest) -> list[dict]:
    return await content_service.menus()


@router.get("/menus/{menu_name}", response=MenuOut, summary="Read one menu")
async def read_menu(request: HttpRequest, menu_name: str, language: str | None = None) -> dict:
    """One menu, one level of nesting deep, with each entry's label translated."""
    try:
        return await content_service.menu(menu_name, resolve_language(request, language))
    except ContentNotFound as missing:
        raise HttpError(404, str(missing)) from None


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
    """
    try:
        return await content_service.page(
            page_name, resolve_language(request, language), preview=preview
        )
    except ContentNotFound as missing:
        raise HttpError(404, str(missing)) from None
