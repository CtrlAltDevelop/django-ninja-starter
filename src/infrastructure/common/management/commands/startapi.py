"""Create and register a versioned Django Ninja API router."""

import json
import re
from pathlib import Path
from typing import Any

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError, CommandParser

from infrastructure.common.registry import (
    ApiRoute,
    api_registry_path,
    load_api_registry,
    normalize_api_version,
)

APP_NAME_PATTERN = re.compile(r"^[a-z][a-z0-9_]*$")


def _class_name(app_name: str) -> str:
    return "".join(part.capitalize() for part in app_name.split("_"))


def _write_new_file(path: Path, content: str) -> None:
    if path.exists():
        raise CommandError(f"Refusing to overwrite existing file: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


class Command(BaseCommand):
    help = "Create and register a versioned Django Ninja API"

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument("name", help="feature app name, for example users or order_items")
        parser.add_argument("--api-version", required=True, help="API version, for example v1")
        parser.add_argument(
            "--prefix",
            help="route prefix (defaults to /<name> with underscores converted to hyphens)",
        )

    def handle(self, *args: Any, **options: Any) -> None:
        app_name: str = options["name"]
        if not APP_NAME_PATTERN.fullmatch(app_name):
            raise CommandError(
                "name must start with a lowercase letter and contain only lowercase "
                "letters, numbers, and underscores"
            )
        try:
            version = normalize_api_version(options["api_version"])
        except ValueError as error:
            raise CommandError(str(error)) from error

        prefix = options["prefix"] or f"/{app_name.replace('_', '-')}"
        if not prefix.startswith("/") or prefix.endswith("/"):
            raise CommandError("prefix must start with '/' and must not end with '/'")

        registry_path = api_registry_path()
        registry = load_api_registry(registry_path)
        module_version = version.replace(".", "_")
        router_path = f"apps.{app_name}.api.{module_version}.router"
        app_config = f"apps.{app_name}.apps.{_class_name(app_name)}Config"
        route: ApiRoute = {
            "app_config": app_config,
            "prefix": prefix,
            "router": router_path,
            "tag": _class_name(app_name),
            # The group heading and blurb Swagger shows for this app. A
            # placeholder in the same spirit as the example endpoint below:
            # true as written, and worth replacing with what the app is for.
            "description": f"Endpoints published by the {app_name} app.",
        }
        routes = registry.setdefault(version, {"routes": []})["routes"]
        if any(item["prefix"] == prefix or item["router"] == router_path for item in routes):
            raise CommandError(f"API route is already registered for {version}: {prefix}")

        app_path = Path(settings.BASE_DIR) / "src" / "apps" / app_name
        version_file = app_path / "api" / f"{module_version}.py"
        if version_file.exists():
            raise CommandError(f"API version already exists: {version_file}")

        if not app_path.exists():
            _write_new_file(app_path / "__init__.py", f'"""{_class_name(app_name)} app."""\n')
            _write_new_file(
                app_path / "apps.py",
                "from django.apps import AppConfig\n\n\n"
                f"class {_class_name(app_name)}Config(AppConfig):\n"
                '    default_auto_field = "django.db.models.BigAutoField"\n'
                f'    name = "apps.{app_name}"\n',
            )
        api_package = app_path / "api" / "__init__.py"
        if not api_package.exists():
            _write_new_file(api_package, '"""Versioned API routers."""\n')

        _write_new_file(
            version_file,
            "from django.http import HttpRequest\n"
            "from ninja import Router, Schema\n\n\n"
            "class ApiMessage(Schema):\n"
            "    message: str\n\n\n"
            "router = Router()\n\n\n"
            '@router.get("/", response=ApiMessage, summary="Example endpoint")\n'
            "def index(request: HttpRequest) -> ApiMessage:\n"
            f'    return ApiMessage(message="{_class_name(app_name)} {version} API")\n',
        )
        tests_package = app_path / "tests" / "__init__.py"
        if not tests_package.exists():
            _write_new_file(tests_package, '"""Feature API tests."""\n')
        _write_new_file(
            app_path / "tests" / f"test_{module_version}.py",
            "from django.test import Client\n\n\n"
            "def test_index() -> None:\n"
            f'    response = Client().get("/api/{version}{prefix}/")\n\n'
            "    assert response.status_code == 200\n"
            '    assert response.json()["data"] == {"message": '
            f'"{_class_name(app_name)} {version} API"}}\n',
        )

        routes.append(route)
        registry_path.write_text(
            f"{json.dumps(registry, indent=2, sort_keys=True)}\n",
            encoding="utf-8",
        )

        endpoint = f"/api/{version}{prefix}/"
        self.stdout.write(self.style.SUCCESS(f"Created {router_path}"))
        self.stdout.write(f"Endpoint: {endpoint}")
        self.stdout.write(f"Swagger: /api/docs (select {version})")
