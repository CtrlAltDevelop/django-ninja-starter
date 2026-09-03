"""Create and register a new feature app, with all three of its transports.

What it writes is the layout every app in this project follows: one service
class holding the decisions, and a ``rest``, ``graph`` and ``grpc`` package that
each render what it returns. Scaffolding only REST would make the two-transport
apps the exception rather than the rule, and the second one never gets written.

The generated ``grpc`` package has no ``.proto`` yet -- run ``manage.py protos``
once the app is registered, which is what compiles the declaration into a
document and its stubs.
"""

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


def _camel(app_name: str) -> str:
    """The name GraphQL will give a field built from this app's snake_case name."""
    head, *rest = app_name.split("_")
    return head + "".join(part.capitalize() for part in rest)


def _write_new_file(path: Path, content: str) -> None:
    if path.exists():
        raise CommandError(f"Refusing to overwrite existing file: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _write_once(path: Path, content: str) -> None:
    """Write a file the app has only one of, however many versions it publishes.

    A second `startapi` against the same app adds a version to `rest/`, and must
    not try to lay down the service and the other two transports again -- they
    already exist, and this is the same app growing, not a new one.
    """
    if not path.exists():
        _write_new_file(path, content)


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
        class_name = _class_name(app_name)
        router_path = f"apps.{app_name}.rest.{module_version}.router"
        app_config = f"apps.{app_name}.apps.{class_name}Config"
        route: ApiRoute = {
            "app_config": app_config,
            "prefix": prefix,
            "router": router_path,
            "tag": class_name,
            # The group heading and blurb Swagger shows for this app. A
            # placeholder in the same spirit as the example endpoint below:
            # true as written, and worth replacing with what the app is for.
            "description": f"Endpoints published by the {app_name} app.",
        }
        routes = registry.setdefault(version, {"routes": []})["routes"]
        if any(item["prefix"] == prefix or item["router"] == router_path for item in routes):
            raise CommandError(f"API route is already registered for {version}: {prefix}")

        app_path = Path(settings.BASE_DIR) / "src" / "apps" / app_name
        version_file = app_path / "rest" / f"{module_version}.py"
        if version_file.exists():
            raise CommandError(f"API version already exists: {version_file}")

        if not app_path.exists():
            _write_new_file(app_path / "__init__.py", f'"""{class_name} app."""\n')
            _write_new_file(
                app_path / "apps.py",
                "from django.apps import AppConfig\n\n\n"
                f"class {class_name}Config(AppConfig):\n"
                '    default_auto_field = "django.db.models.BigAutoField"\n'
                f'    name = "apps.{app_name}"\n',
            )
        _write_once(
            app_path / "rest" / "__init__.py",
            f'"""Versioned HTTP routers for the {app_name} app.\n\n'
            "One module per API version, and the registry names the version it\n"
            "wants. The infrastructure apps export a single `router` here instead,\n"
            'because none of them is versioned.\n"""\n',
        )

        _write_new_file(
            version_file,
            f'"""What the {app_name} app publishes over HTTP.\n\n'
            f"Every decision belongs to {class_name}Service; this file renders what\n"
            'it returns.\n"""\n\n'
            "from django.http import HttpRequest\n"
            "from ninja import Router\n\n"
            f"from apps.{app_name}.rest.schemas import ApiMessage\n"
            f"from apps.{app_name}.services import {app_name}_service\n\n\n"
            "router = Router()\n\n\n"
            '@router.get("/", response=ApiMessage, summary="Example endpoint")\n'
            "def index(request: HttpRequest) -> ApiMessage:\n"
            f"    return ApiMessage(message={app_name}_service.greeting())\n",
        )
        _write_once(
            app_path / "rest" / "schemas.py",
            f'"""The contract the {app_name} endpoints publish."""\n\n'
            "from ninja import Schema\n\n\n"
            "class ApiMessage(Schema):\n"
            "    message: str\n",
        )
        _write_once(
            app_path / "graph" / "__init__.py",
            f'"""The GraphQL transport for the {app_name} app."""\n\n'
            f"from apps.{app_name}.graph.schema import Query\n\n"
            '__all__ = ["Query"]\n',
        )
        _write_once(
            app_path / "graph" / "schema.py",
            f'"""The {app_name} app\'s contribution to the project\'s GraphQL schema.\n\n'
            "Merged into the root query by `config.graph`. Field names share one\n"
            "namespace with every other installed app, so prefix them with the app\n"
            'name as this one does.\n"""\n\n'
            "import strawberry\n\n"
            f"from apps.{app_name}.services import {app_name}_service\n"
            "from infrastructure.common.graph.errors import resolver\n\n\n"
            "@strawberry.type\n"
            "class Query:\n"
            f'    @strawberry.field(description="An example {app_name} field.")\n'
            "    @resolver\n"
            f"    def {app_name}_greeting(self) -> str:\n"
            f"        return {app_name}_service.greeting()\n",
        )
        _write_once(
            app_path / "grpc" / "__init__.py",
            f'"""The gRPC transport for the {app_name} app.\n\n'
            "The generated stubs live here beside the ``.proto`` that produced them.\n"
            'Regenerate with ``python manage.py protos``.\n"""\n',
        )
        _write_once(
            app_path / "grpc" / "services.py",
            f'"""What the {app_name} app publishes over gRPC.\n\n'
            "The message shapes are declared on the actions; ``manage.py protos``\n"
            'turns them into a ``.proto`` and its stubs.\n"""\n\n'
            "from typing import Any\n\n"
            "from asgiref.sync import sync_to_async\n"
            "from django_socio_grpc import generics\n"
            "from django_socio_grpc.decorators import grpc_action\n\n"
            f"from apps.{app_name}.services import {app_name}_service\n"
            "from infrastructure.common.grpc.errors import action\n\n\n"
            f"class {class_name}Service(generics.GenericService):\n"
            f'    """The example call, answered by the same service REST calls."""\n\n'
            "    @grpc_action(\n"
            "        request=[],\n"
            '        response=[{"name": "message", "type": "string"}],\n'
            '        response_name="ApiMessage",\n'
            "    )\n"
            "    @action\n"
            "    async def Index(self, request: Any, context: Any) -> Any:\n"
            f"        from apps.{app_name}.grpc import {app_name}_pb2\n\n"
            f"        message = await sync_to_async({app_name}_service.greeting)()\n"
            f"        return {app_name}_pb2.ApiMessage(message=message)\n\n\n"
            f"GRPC_SERVICES = [{class_name}Service]\n",
        )
        _write_once(
            app_path / "services.py",
            f'"""Everything the {app_name} app decides, independent of who asked.\n\n'
            "The three transport packages beside this file render what these methods\n"
            "return. Put the rules here, so REST, GraphQL and gRPC cannot drift.\n"
            '"""\n\n\n'
            f"class {class_name}Service:\n"
            f'    """The {app_name} app\'s own operations."""\n\n'
            "    def greeting(self) -> str:\n"
            f'        return "{class_name} {version} API"\n\n\n'
            f"{app_name}_service = {class_name}Service()\n",
        )
        tests_package = app_path / "tests" / "__init__.py"
        if not tests_package.exists():
            _write_new_file(tests_package, '"""Feature API tests."""\n')
        _write_new_file(
            app_path / "tests" / f"test_{module_version}.py",
            '"""The example endpoint, over each transport it is published on."""\n\n'
            "import json\n\n"
            "from django.test import Client\n\n\n"
            "def test_index() -> None:\n"
            f'    response = Client().get("/api/{version}{prefix}/")\n\n'
            "    assert response.status_code == 200\n"
            '    assert response.json()["data"] == {"message": '
            f'"{class_name} {version} API"}}\n\n\n'
            "def test_the_graphql_field_answers_the_same() -> None:\n"
            "    response = Client().post(\n"
            '        "/graphql",\n'
            f'        data={{"query": "{{ {_camel(app_name)}Greeting }}"}},\n'
            '        content_type="application/json",\n'
            "    )\n\n"
            "    assert response.status_code == 200\n"
            '    assert json.loads(response.content)["data"] == {\n'
            f'        "{_camel(app_name)}Greeting": "{class_name} {version} API"\n'
            "    }\n",
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
        self.stdout.write("GraphQL: /graphql")
        self.stdout.write("gRPC:    run `python manage.py protos` to compile its .proto")
