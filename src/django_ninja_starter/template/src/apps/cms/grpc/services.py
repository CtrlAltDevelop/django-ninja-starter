"""Reading content over gRPC: five read-only calls.

Writing is the admin's job here as everywhere else. The language arrives as a
field on the request rather than as a header, and an empty one means the
configured default -- gRPC has no ``Accept-Language`` to negotiate with.
"""

import json
from typing import Any

from django_socio_grpc import generics
from django_socio_grpc.decorators import grpc_action

from apps.cms.grpc.serializers import MenuItem, MenuSummary, PageMeta, PageSummary, Section
from apps.cms.services import ContentNotFound, content_service
from apps.cms.translations import default_language, match_language
from infrastructure.common.errors import ApiError
from infrastructure.common.grpc.errors import action
from infrastructure.common.responses import ResponseTitle

LANGUAGE_REQUEST = [{"name": "language", "type": "string"}]


def _pb2() -> Any:
    from apps.cms.grpc import cms_pb2

    return cms_pb2


def _language(requested: str) -> str:
    """Obey an explicit language, and fall back to the site's default."""
    return match_language(requested) or default_language()


def _missing(error: ContentNotFound) -> ApiError:
    return ApiError(str(error), status=404, title=ResponseTitle.NOT_FOUND)


def _field(field: dict[str, Any]) -> Any:
    return _pb2().ContentField(
        id=field["id"],
        name=field["name"],
        type=str(field["type"]),
        multiple=field["multiple"],
        required=field["required"],
        value_json=json.dumps(field.get("value"), default=str),
    )


def _section(section: dict[str, Any]) -> Any:
    return _pb2().Section(
        id=section["id"],
        name=section["name"],
        shared=section.get("shared", False),
        fields=[_field(field) for field in section.get("fields", [])],
    )


def _flattened(sections: list[dict[str, Any]]) -> list[Any]:
    """Walk the section tree depth-first into the flat list the message carries."""
    flat: list[Any] = []
    for section in sections:
        flat.append(_section(section))
        flat.extend(_flattened(section.get("children", [])))
    return flat


class ContentService(generics.GenericService):
    """The five reads the REST router publishes under `/cms`."""

    @grpc_action(
        request=LANGUAGE_REQUEST,
        request_name="PagesRequest",
        response=[{"name": "pages", "cardinality": "repeated", "type": PageSummary}],
        response_name="PageList",
    )
    @action
    async def Pages(self, request: Any, context: Any) -> Any:
        pages = await content_service.pages(_language(request.language))
        pb2 = _pb2()
        return pb2.PageList(
            pages=[
                pb2.PageSummary(
                    id=page["id"],
                    name=page["name"],
                    title=page["title"],
                    order=page["order"],
                    updated_at=page["updated_at"].isoformat(),
                )
                for page in pages
            ]
        )

    @grpc_action(
        request=LANGUAGE_REQUEST,
        request_name="SiteRequest",
        response=[
            {"name": "language", "type": "string"},
            {"name": "languages", "cardinality": "repeated", "type": "string"},
            {"name": "default_language", "type": "string"},
            {"name": "name", "type": "string"},
            {"name": "tagline", "type": "string"},
            {"name": "description", "type": "string"},
            {"name": "og_title", "type": "string"},
            {"name": "og_description", "type": "string"},
            {"name": "logo", "type": "string"},
            {"name": "favicon", "type": "string"},
            {"name": "og_image", "type": "string"},
            {"name": "og_url", "type": "string"},
            {"name": "contact_json", "type": "string"},
            {"name": "social_links_json", "type": "string"},
            {"name": "extra_json", "type": "string"},
        ],
        response_name="SiteInfo",
    )
    @action
    async def Site(self, request: Any, context: Any) -> Any:
        site = await content_service.site(_language(request.language))
        return _pb2().SiteInfo(
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
            contact_json=json.dumps(site.get("contact", {})),
            social_links_json=json.dumps(site.get("social_links", []), default=str),
            extra_json=json.dumps(site.get("extra", {}), default=str),
        )

    @grpc_action(
        request=[],
        response=[{"name": "menus", "cardinality": "repeated", "type": MenuSummary}],
        response_name="MenuList",
    )
    @action
    async def Menus(self, request: Any, context: Any) -> Any:
        pb2 = _pb2()
        return pb2.MenuList(
            menus=[
                pb2.MenuSummary(id=menu["id"], name=menu["name"])
                for menu in await content_service.menus()
            ]
        )

    @grpc_action(
        request=[
            {"name": "name", "type": "string"},
            {"name": "language", "type": "string"},
        ],
        request_name="MenuRequest",
        response=[
            {"name": "id", "type": "string"},
            {"name": "name", "type": "string"},
            {"name": "items", "cardinality": "repeated", "type": MenuItem},
        ],
        response_name="MenuDetail",
    )
    @action
    async def Menu(self, request: Any, context: Any) -> Any:
        try:
            menu = await content_service.menu(request.name, _language(request.language))
        except ContentNotFound as error:
            raise _missing(error) from None
        pb2 = _pb2()
        return pb2.MenuDetail(
            id=menu["id"],
            name=menu["name"],
            items=[
                pb2.MenuItem(
                    label=item["label"],
                    page=item.get("page") or "",
                    url=item.get("url") or "",
                    new_tab=item.get("new_tab", False),
                )
                for item in menu.get("items", [])
            ],
        )

    @grpc_action(
        request=[
            {"name": "name", "type": "string"},
            {"name": "language", "type": "string"},
            {"name": "preview", "type": "string"},
        ],
        request_name="PageRequest",
        response=[
            {"name": "id", "type": "string"},
            {"name": "name", "type": "string"},
            {"name": "language", "type": "string"},
            {"name": "status", "type": "string"},
            {"name": "meta", "type": PageMeta},
            {"name": "sections", "cardinality": "repeated", "type": Section},
        ],
        response_name="PageDetail",
    )
    @action
    async def Page(self, request: Any, context: Any) -> Any:
        try:
            page = await content_service.page(
                request.name,
                _language(request.language),
                preview=request.preview or None,
            )
        except ContentNotFound as error:
            raise _missing(error) from None
        meta = page["meta"]
        pb2 = _pb2()
        return pb2.PageDetail(
            id=page["id"],
            name=page["name"],
            language=page["language"],
            status=page["status"],
            meta=pb2.PageMeta(
                title=meta["title"],
                description=meta.get("description") or "",
                og_title=meta.get("og_title", ""),
                og_description=meta.get("og_description", ""),
                og_image=meta.get("og_image", ""),
                og_url=meta.get("og_url", ""),
            ),
            sections=_flattened(page.get("sections", [])),
        )


GRPC_SERVICES = [ContentService]
