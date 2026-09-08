"""Reading content over gRPC.

Public, like the routes: no credential, because reading a published site needs
none. `transactional_db` throughout -- the server answers on its own connection.
"""

import json
from collections.abc import Callable
from typing import Any

import grpc
import pytest
from google.protobuf.empty_pb2 import Empty

from apps.cms.grpc import cms_pb2, cms_pb2_grpc
from apps.cms.models import Page, SiteSettings

Stub = cms_pb2_grpc.ContentControllerStub


@pytest.fixture
def published(transactional_db: None, home: Page) -> Page:
    """The shared `home` fixture, on a connection the server can also see."""
    return home


def test_it_lists_the_published_pages(published: Page, grpc_call: Callable[..., Any]) -> None:
    reply = grpc_call(Stub, "Pages", cms_pb2.PagesRequest())

    assert [page.id for page in reply.pages] == ["home"]


def test_a_draft_is_not_found_without_a_preview_token(
    transactional_db: None, draft: Page, grpc_call: Callable[..., Any]
) -> None:
    with pytest.raises(grpc.aio.AioRpcError) as refusal:
        grpc_call(Stub, "Page", cms_pb2.PageRequest(name=draft.slug))

    assert refusal.value.code() is grpc.StatusCode.NOT_FOUND


def test_a_page_carries_its_sections_flattened(
    published: Page, grpc_call: Callable[..., Any]
) -> None:
    """Protobuf has no recursive shorthand, so the tree arrives depth-first."""
    reply = grpc_call(Stub, "Page", cms_pb2.PageRequest(name="home"))

    names = [section.id for section in reply.sections]
    assert "hero" in names
    assert len(names) > 1


def test_a_field_carries_its_value_as_json_beside_its_type(
    published: Page, grpc_call: Callable[..., Any]
) -> None:
    """The type is what tells a client how to read the JSON next to it."""
    reply = grpc_call(Stub, "Page", cms_pb2.PageRequest(name="home"))

    field = next(
        field for section in reply.sections for field in section.fields if field.id == "headline"
    )
    assert field.type
    assert json.loads(field.value_json) is not None


def test_the_site_answers_before_anybody_has_filled_the_form_in(
    transactional_db: None, grpc_call: Callable[..., Any]
) -> None:
    """Empty strings rather than a refusal, as over HTTP."""
    assert not SiteSettings.objects.exists()

    reply = grpc_call(Stub, "Site", cms_pb2.SiteRequest())

    assert reply.name == ""
    assert json.loads(reply.contact_json) == {}


def test_menus_are_listed(transactional_db: None, grpc_call: Callable[..., Any]) -> None:
    reply = grpc_call(Stub, "Menus", Empty())

    assert list(reply.menus) == []
