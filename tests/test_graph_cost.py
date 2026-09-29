"""What one GraphQL request is allowed to cost.

A REST caller is bounded by the route they picked. A GraphQL caller writes the
query, so the only thing between a schema with a cycle in it and a table scan
per request is a limit checked before the resolvers run. Every one of these
asserts a hostile document is refused *and* that an ordinary one still is not --
a limit that only ever says no is indistinguishable from a broken endpoint.
"""

import json
from typing import Any

import pytest
from django.test import Client

from config.graph import cost_extensions

pytestmark = pytest.mark.django_db


def _ask(client: Client, query: str) -> dict[str, Any]:
    reply = client.post("/graphql", json.dumps({"query": query}), content_type="application/json")
    return dict(reply.json())


def test_a_query_nested_past_the_budget_is_refused(client: Client) -> None:
    """A category node's `children` are category nodes, so the type recurses.

    Fifteen levels of a cycle is the whole attack in one line: nothing stops a
    caller writing a hundred, and each level multiplies the rows underneath it.
    """
    query = "{ shopCategories " + "{ children " * 15 + "{ id }" + " }" * 15 + " }"
    answer = _ask(client, query)
    assert "errors" in answer
    assert "maximum operation depth" in answer["errors"][0]["message"]


def test_a_query_inside_the_depth_budget_is_served(client: Client) -> None:
    answer = _ask(client, "{ shopCategories { children { children { id } } } }")
    assert "errors" not in answer


def test_the_same_costly_field_under_many_aliases_is_refused(client: Client) -> None:
    """Aliases are the attack a depth limit cannot see.

    Forty copies of one top-level field is forty scans at depth one, which every
    nesting limit in the world will wave through.
    """
    aliases = " ".join(f"a{index}: shopProducts(limit: 100) {{ total }}" for index in range(40))
    answer = _ask(client, "{ " + aliases + " }")
    assert "errors" in answer
    assert "aliases found" in answer["errors"][0]["message"]


def test_an_enormous_document_is_refused_before_it_is_parsed(client: Client) -> None:
    """Rejected at the lexer, so a megabyte of query costs no parse tree."""
    answer = _ask(client, "{ " + " ".join(["ok"] * 3000) + " }")
    assert "errors" in answer
    assert "tokens" in answer["errors"][0]["message"]


def test_an_ordinary_query_is_untouched_by_any_of_it(client: Client) -> None:
    assert _ask(client, "{ ok }") == {"data": {"ok": True}}


def test_introspection_is_published_by_default(client: Client) -> None:
    """On, because a public API's schema is usually meant to be public."""
    answer = _ask(client, "{ __schema { queryType { name } } }")
    assert "errors" not in answer


def test_a_deployment_can_turn_every_limit_off(settings: Any) -> None:
    """Zero means off, one limit at a time.

    A deployment that has measured its own schema is allowed to disagree with
    these numbers without having to disagree with all of them.
    """
    settings.GRAPHQL_MAX_DEPTH = 0
    settings.GRAPHQL_MAX_ALIASES = 0
    settings.GRAPHQL_MAX_TOKENS = 0
    settings.GRAPHQL_INTROSPECTION = True
    names = {getattr(part, "__name__", type(part).__name__) for part in cost_extensions()}
    assert "ParserCache" in names
    assert not {"QueryDepthLimiter", "MaxAliasesLimiter", "MaxTokensLimiter"} & names


def test_introspection_can_be_withheld(settings: Any) -> None:
    settings.GRAPHQL_INTROSPECTION = False
    names = {getattr(part, "__name__", type(part).__name__) for part in cost_extensions()}
    assert "DisableIntrospection" in names
