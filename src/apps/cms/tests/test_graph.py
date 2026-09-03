"""Reading content over GraphQL.

Same service as the routes, so the same answers: a draft is not there, a preview
token opens exactly one page, and the language argument beats the header.
"""

import json
from typing import Any

import pytest
from django.test import Client

from apps.cms.models import Page

pytestmark = pytest.mark.django_db

PAGES = "query($language: String) { cmsPages(language: $language) { id name title } }"
SITE = "query($language: String) { cmsSite(language: $language) { language name tagline contact } }"
MENUS = "{ cmsMenus { id name } }"
PAGE = """
query($name: String!, $language: String, $preview: String) {
  cmsPage(name: $name, language: $language, preview: $preview) {
    id
    status
    meta { title }
    sections { id name fields { id type value } }
  }
}
"""


def graphql(query: str, **variables: Any) -> dict[str, Any]:
    response = Client().post(
        "/graphql",
        data={"query": query, "variables": variables},
        content_type="application/json",
    )
    assert response.status_code == 200, response.content
    return json.loads(response.content)


def test_it_lists_the_name_to_show_and_the_name_to_ask_for(home: Page) -> None:
    [page] = graphql(PAGES)["data"]["cmsPages"]

    assert page["id"] == "home"
    assert page["name"] == "Home"


def test_a_draft_is_not_listed(home: Page, draft: Page) -> None:
    listed = [page["id"] for page in graphql(PAGES)["data"]["cmsPages"]]

    assert listed == ["home"]


def test_a_draft_is_not_found_without_a_preview_token(draft: Page) -> None:
    body = graphql(PAGE, name=draft.slug)

    assert body["data"] is None
    assert body["errors"][0]["extensions"]["title"] == "NOT_FOUND"
    assert body["errors"][0]["extensions"]["status"] == 404


def test_the_site_answers_with_its_free_form_fields_intact(site: Any) -> None:
    """`contact` is a JSON scalar here, where REST has an object -- same content."""
    body = graphql(SITE)["data"]["cmsSite"]

    assert body["name"] == "Acme"
    assert body["contact"] == {"email": "hello@example.com"}


def test_a_language_argument_is_obeyed(site: Any) -> None:
    persian = graphql(SITE, language="fa")["data"]["cmsSite"]

    assert persian["language"] == "fa"
    assert persian["name"] == "\u0622\u06a9\u0645\u06cc"


def test_an_untranslated_line_falls_back(site: Any) -> None:
    """The same fallback the routes use -- the decision is the service's."""
    assert graphql(SITE, language="fa")["data"]["cmsSite"]["tagline"] == "We make things"


def test_a_page_carries_its_sections_and_typed_fields(home: Page) -> None:
    page = graphql(PAGE, name="home")["data"]["cmsPage"]

    assert page["id"] == "home"
    assert page["sections"]
    assert all("type" in field for section in page["sections"] for field in section["fields"])
