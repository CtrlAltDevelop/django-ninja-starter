"""`startapi` scaffolds a whole app, not only a router.

The point of the assertions below is that all three transports arrive together.
An app scaffolded with REST alone is an app whose GraphQL field and RPC never
get written, and the parity the project promises quietly stops being true.
"""

import json
from pathlib import Path

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import override_settings


def _write_registry(base_dir: Path) -> Path:
    registry_path = base_dir / "src" / "config" / "api_registry.json"
    registry_path.parent.mkdir(parents=True)
    registry_path.write_text(
        json.dumps(
            {
                "v1": {
                    "routes": [
                        {
                            "app_config": "infrastructure.common.apps.CommonConfig",
                            "prefix": "/health",
                            "router": "infrastructure.common.rest.api.router",
                            "tag": "Health",
                        }
                    ]
                }
            }
        )
    )
    return registry_path


def test_startapi_creates_and_registers_versioned_router(tmp_path: Path) -> None:
    registry_path = _write_registry(tmp_path)

    with override_settings(BASE_DIR=tmp_path):
        call_command("startapi", "order_items", api_version="v2.1")

    app_path = tmp_path / "src" / "apps" / "order_items"
    assert (app_path / "apps.py").is_file()
    assert (app_path / "services.py").is_file()
    assert (app_path / "rest" / "v2_1.py").is_file()
    assert (app_path / "rest" / "schemas.py").is_file()
    assert (app_path / "graph" / "schema.py").is_file()
    assert (app_path / "grpc" / "services.py").is_file()
    assert (app_path / "tests" / "test_v2_1.py").is_file()
    registry = json.loads(registry_path.read_text())
    assert registry["v2.1"]["routes"] == [
        {
            "app_config": "apps.order_items.apps.OrderItemsConfig",
            "prefix": "/order-items",
            "router": "apps.order_items.rest.v2_1.router",
            "tag": "OrderItems",
            "description": "Endpoints published by the order_items app.",
        }
    ]


@pytest.mark.parametrize(
    ("name", "version", "prefix"),
    [("BadName", "v1", None), ("users", "latest", None), ("users", "v1", "users/")],
)
def test_startapi_rejects_invalid_arguments(
    tmp_path: Path,
    name: str,
    version: str,
    prefix: str | None,
) -> None:
    _write_registry(tmp_path)

    with override_settings(BASE_DIR=tmp_path), pytest.raises(CommandError):
        call_command("startapi", name, api_version=version, prefix=prefix)


def test_the_scaffolded_app_puts_its_decisions_in_the_service(tmp_path: Path) -> None:
    """Each transport calls the service; none of them holds the answer itself."""
    _write_registry(tmp_path)

    with override_settings(BASE_DIR=tmp_path):
        call_command("startapi", "order_items", api_version="v1")

    app_path = tmp_path / "src" / "apps" / "order_items"
    service = (app_path / "services.py").read_text()

    assert "class OrderItemsService" in service
    for transport in ("rest/v1.py", "graph/schema.py", "grpc/services.py"):
        # The gRPC action crosses over with `sync_to_async`, so it names the
        # method rather than calling it inline -- the service is still the caller.
        assert "order_items_service.greeting" in (app_path / transport).read_text()


def test_the_scaffolded_files_are_valid_python(tmp_path: Path) -> None:
    """Generated code that does not parse is a worse start than no code at all."""
    import ast

    _write_registry(tmp_path)

    with override_settings(BASE_DIR=tmp_path):
        call_command("startapi", "order_items", api_version="v1")

    for module in (tmp_path / "src" / "apps" / "order_items").rglob("*.py"):
        ast.parse(module.read_text(), filename=str(module))
