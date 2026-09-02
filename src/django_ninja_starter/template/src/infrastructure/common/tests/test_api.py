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
    response = Client().get("/api/docs")

    assert response.status_code == 200
    assert b'"urls"' in response.content
    assert b"/api/v1/openapi.json" in response.content


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
