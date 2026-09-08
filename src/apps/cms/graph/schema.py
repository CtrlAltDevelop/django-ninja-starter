"""The content app's contribution to the project's GraphQL schema.

Read-only, like the routes: writing content is the admin's job. The language is
an argument here rather than a query parameter, and falls back to the same
``Accept-Language`` negotiation when it is not given -- one language decision,
made in one place, reached two ways.
"""

from typing import Any

import strawberry
from strawberry.types import Info

from apps.cms.graph.types import (
    MenuSummaryType,
    MenuType,
    PageSummaryType,
    PageType,
    SiteType,
    menu_type,
    page_summary_type,
    page_type,
    site_type,
)
from apps.cms.services import ContentNotFound, content_service
from apps.cms.translations import resolve_language
from infrastructure.common.errors import ApiError
from infrastructure.common.graph.errors import async_resolver
from infrastructure.common.responses import ResponseTitle


def _missing(error: ContentNotFound) -> ApiError:
    return ApiError(str(error), status=404, title=ResponseTitle.NOT_FOUND)


@strawberry.type
class Query:
    @strawberry.field(description="Every published page, in menu order.")
    @async_resolver
    async def cms_pages(
        self, info: Info[Any, Any], language: str | None = None
    ) -> list[PageSummaryType]:
        chosen = resolve_language(info.context.request, language)
        return [page_summary_type(page) for page in await content_service.pages(chosen)]

    @strawberry.field(description="The site metadata: titles, logo, contact details.")
    @async_resolver
    async def cms_site(self, info: Info[Any, Any], language: str | None = None) -> SiteType:
        chosen = resolve_language(info.context.request, language)
        return site_type(await content_service.site(chosen))

    @strawberry.field(description="Every menu an editor has defined.")
    @async_resolver
    async def cms_menus(self, info: Info[Any, Any]) -> list[MenuSummaryType]:
        return [
            MenuSummaryType(id=menu["id"], name=menu["name"])
            for menu in await content_service.menus()
        ]

    @strawberry.field(description="One menu, with each entry's label translated.")
    @async_resolver
    async def cms_menu(
        self, info: Info[Any, Any], name: str, language: str | None = None
    ) -> MenuType:
        chosen = resolve_language(info.context.request, language)
        try:
            return menu_type(await content_service.menu(name, chosen))
        except ContentNotFound as error:
            raise _missing(error) from None

    @strawberry.field(description="One page: metadata, sections, and every field.")
    @async_resolver
    async def cms_page(
        self,
        info: Info[Any, Any],
        name: str,
        language: str | None = None,
        preview: str | None = None,
    ) -> PageType:
        chosen = resolve_language(info.context.request, language)
        try:
            return page_type(await content_service.page(name, chosen, preview=preview))
        except ContentNotFound as error:
            raise _missing(error) from None
