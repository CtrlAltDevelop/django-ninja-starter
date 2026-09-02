from unittest.mock import MagicMock, patch

from django.db.utils import OperationalError
from django.test import Client

from infrastructure.common.services import database_is_ready


def test_liveness() -> None:
    response = Client().get("/api/v1/health/live")

    assert response.status_code == 200
    assert response.json()["data"] == {"status": "ok", "checks": {}}


def test_readiness(db: None) -> None:
    response = Client().get("/api/v1/health/ready")

    assert response.status_code == 200
    assert response.json()["data"] == {"status": "ok", "checks": {"database": "ok"}}


def test_versioned_swagger_lists_registered_openapi_specs() -> None:
    from infrastructure.common.registry import load_api_registry

    response = Client().get("/api/docs")
    page = response.content.decode()

    assert response.status_code == 200
    assert '"urls"' in page
    for version in load_api_registry():
        assert f"/api/{version}/openapi.json" in page


def test_the_swagger_page_can_actually_render_that_selector() -> None:
    """The `urls` list is read by the topbar, which two other settings supply.

    Without them the page still answers 200, still carries the list, and still
    shows "No API definition provided" -- it never fetches a document at all.
    So this asserts the parts that make the list mean something: the standalone
    preset script, and the layout that renders the topbar reading it.
    """
    page = Client().get("/api/docs").content.decode()

    assert "swagger-ui-standalone-preset.js" in page
    assert '"layout": "StandaloneLayout"' in page
    assert "SwaggerUIStandalonePreset" in page
    # A member of the bundle rather than the standalone script's own global is
    # undefined at runtime, which is the shape the original bug took.
    assert "SwaggerUIBundle.SwaggerUIStandalonePreset" not in page


def test_versioned_openapi_schema_contains_health_routes() -> None:
    response = Client().get("/api/v1/openapi.json")

    assert response.status_code == 200
    assert "/api/v1/health/live" in response.json()["paths"]
    assert "/api/v1/health/ready" in response.json()["paths"]


@patch("infrastructure.common.api.database_is_ready", return_value=False)
def test_readiness_returns_503_when_database_is_unavailable(
    database_ready: object,
) -> None:
    response = Client().get("/api/v1/health/ready")

    assert response.status_code == 503
    assert response.json()["data"] == {
        "status": "unavailable",
        "checks": {"database": "unavailable"},
    }


@patch("infrastructure.common.services.connections")
def test_database_readiness_handles_operational_error(connections: MagicMock) -> None:
    connections.__getitem__.side_effect = OperationalError

    assert database_is_ready() is False
