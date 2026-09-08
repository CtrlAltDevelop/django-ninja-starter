"""Every app that publishes an API publishes it three ways.

This is the promise the refactor makes, and it is the one that quietly rots: a
new endpoint is added to a router, and the GraphQL field and the RPC that were
supposed to sit beside it never appear. So the shape is asserted rather than
trusted -- each app has the three packages, each transport is actually wired
into the project, and each app's `.proto` is the one its services would produce
right now.
"""

import json
import os
import subprocess
import sys
from importlib import import_module
from pathlib import Path

import pytest
from django.apps import AppConfig, apps

ROOT = Path(__file__).resolve().parents[1]


def publishing_apps() -> list[AppConfig]:
    """Every installed app of this project's own that mounts a router.

    An app with a ``rest`` package but no ``router`` in it is shared vocabulary
    rather than a transport -- ``auth.core`` and ``oauth.core`` hold the response
    contracts every method answers with -- and has nothing of its own to publish.
    """
    found = []
    for config in apps.get_app_configs():
        if not config.name.startswith(("apps.", "infrastructure.")):
            continue
        if not (Path(config.path) / "rest").is_dir():
            continue
        if getattr(import_module(f"{config.name}.rest"), "router", None) is None:
            continue
        found.append(config)
    return found


def app_ids() -> list[str]:
    return [config.name for config in publishing_apps()]


@pytest.fixture(params=publishing_apps(), ids=app_ids())
def published(request: pytest.FixtureRequest) -> AppConfig:
    config: AppConfig = request.param
    return config


def test_the_suite_is_actually_exercising_the_apps() -> None:
    """A guard on the guards: an empty list would make every test below vacuous."""
    assert len(publishing_apps()) >= 10


def test_an_app_with_rest_routes_also_speaks_graphql_and_grpc(published: AppConfig) -> None:
    path = Path(published.path)

    assert (path / "graph").is_dir(), f"{published.name} publishes REST but no GraphQL"
    assert (path / "grpc").is_dir(), f"{published.name} publishes REST but no gRPC"


def test_every_app_declares_its_graphql_contribution(published: AppConfig) -> None:
    """A ``graph`` package that declares neither name is a directory, not a transport.

    The value may be ``None`` -- the three token modes are all installed under
    ``DJANGO_OAUTH_MODE=all`` and only the active one contributes -- but the
    module has to have made that decision on purpose.
    """
    module = import_module(f"{published.name}.graph.schema")

    assert hasattr(module, "Query") or hasattr(module, "Mutation"), (
        f"{published.name}.graph.schema declares neither a Query nor a Mutation"
    )


def test_every_app_registers_at_least_one_grpc_service(published: AppConfig) -> None:
    module = import_module(f"{published.name}.grpc.services")

    assert getattr(module, "GRPC_SERVICES", []), f"{published.name} registers no gRPC service"


def test_every_app_ships_a_generated_proto_and_its_stubs(published: AppConfig) -> None:
    grpc_dir = Path(published.path) / "grpc"
    protos = list(grpc_dir.glob("*.proto"))

    assert protos, f"{published.name} has no .proto file; run `manage.py protos`"
    for proto in protos:
        for suffix in ("_pb2.py", "_pb2_grpc.py", "_pb2.pyi"):
            stub = proto.with_name(f"{proto.stem}{suffix}")
            assert stub.exists(), f"{stub.name} is missing; run `manage.py protos`"


def test_the_graphql_schema_carries_a_field_from_every_installed_app() -> None:
    """The merge is where a contribution silently disappears, so count the doors.

    `config.graph` refuses a repeated attribute name outright; this catches the
    other failure -- an app whose contribution was never collected at all.
    """
    from config.graph import graph_contributions

    labels = {label for label, _ in graph_contributions("Query")}
    labels |= {label for label, _ in graph_contributions("Mutation")}

    assert {"accounts", "common", "auth_password", "cms", "notifications"} <= labels


def test_the_grpc_hook_registers_every_installed_app() -> None:
    from config.grpc import grpc_app_services

    labels = {config.label for config, _ in grpc_app_services(serving=False)}

    assert {"accounts", "common", "auth_password", "cms", "notifications"} <= labels


@pytest.mark.slow
def test_the_committed_protos_match_what_the_services_declare() -> None:
    """`manage.py protos --check` in a subprocess, because it is the real check.

    A `.proto` edited by hand, or left behind when an action changed, is a
    contract that says one thing while the server does another.
    """
    result = subprocess.run(
        [sys.executable, "manage.py", "protos", "--check"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        env={"DJANGO_SETTINGS_MODULE": "config.settings.test", "PATH": "/usr/bin:/bin"},
    )

    assert result.returncode == 0, result.stdout + result.stderr


def test_the_graphql_editor_is_on_in_the_development_settings() -> None:
    """The one place the in-browser editor is wanted is the one it was off in.

    `base` reads its own `DEBUG` -- always False -- so a default derived from it
    could never see `development.py` raising it a moment later, and the editor
    was off exactly where it is meant to be on. Strawberry answers a browser GET
    with a 404 when it has no editor to render, which is a confusing way to be
    told a setting did not take.
    """
    settings = _settings_module("config.settings.development")

    assert settings["DEBUG"] is True
    assert settings["GRAPHQL_GRAPHIQL"] is True


def test_the_graphql_editor_is_off_by_default() -> None:
    """Left on, it is an unauthenticated schema browser.

    Asserted against `base`, which is what `production` inherits and what a
    deployment that names neither gets.
    """
    settings = _settings_module("config.settings.base")

    assert settings["DEBUG"] is False
    assert settings["GRAPHQL_GRAPHIQL"] is False


def _settings_module(dotted: str) -> dict[str, object]:
    """Read one settings module's values in a child process.

    Django holds one settings module for the life of a process and this suite is
    already running under another, so importing it here would either be refused
    or answer for the wrong one.
    """
    script = (
        "import json, sys; sys.path.insert(0, 'src');"
        f"from {dotted} import DEBUG, GRAPHQL_GRAPHIQL;"
        "print(json.dumps({'DEBUG': DEBUG, 'GRAPHQL_GRAPHIQL': GRAPHQL_GRAPHIQL}))"
    )
    result = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        cwd=ROOT,
        # From nothing, so this machine's own `.env` -- which may well set
        # `DJANGO_GRAPHQL_GRAPHIQL` -- does not decide what the shipped default is.
        env={"PATH": os.environ["PATH"], "DJANGO_ENV_FILE": ""},
    )
    assert result.returncode == 0, result.stdout + result.stderr
    values: dict[str, object] = json.loads(result.stdout)
    return values
