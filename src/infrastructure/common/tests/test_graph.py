"""The health checks, asked for over GraphQL rather than over HTTP."""

import json
from typing import Any
from unittest.mock import patch

from django.test import Client

from infrastructure.common.services import HealthService


def graphql(query: str) -> dict[str, Any]:
    """Post one query and return the parsed GraphQL response."""
    response = Client().post("/graphql", data={"query": query}, content_type="application/json")
    assert response.status_code == 200, response.content
    return json.loads(response.content)


def test_liveness_answers_with_the_same_report_the_rest_route_renders() -> None:
    body = graphql("{ liveness { status isReady checks { name status } } }")

    assert body["data"]["liveness"] == {"status": "ok", "isReady": True, "checks": []}


def test_readiness_reports_the_database_it_checked(db: None) -> None:
    body = graphql("{ readiness { status isReady checks { name status } } }")

    assert body["data"]["readiness"] == {
        "status": "ok",
        "isReady": True,
        "checks": [{"name": "database", "status": "ok"}],
    }


@patch.object(HealthService, "database_is_ready", return_value=False)
def test_an_unready_database_is_a_field_here_rather_than_a_status_code(
    database_ready: object,
) -> None:
    """GraphQL answers 200 and says so in the body; REST answers 503.

    Two transports over one service, each saying the same thing the way its own
    protocol says things. Nothing re-decides what ready means.
    """
    body = graphql("{ readiness { status isReady } }")

    assert body["data"]["readiness"] == {"status": "unavailable", "isReady": False}
