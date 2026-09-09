"""Write the reference half of each app's documentation from the app itself.

Documentation that restates what the code says drifts the moment somebody renames
a field, and a stale route table is worse than none: it is a promise the API does
not keep. So the parts that *are* facts about the code -- routes, models, admin
behaviour, required settings -- are generated here, and only prose is written by
hand.

Both live in the same file. Generated sections sit between markers:

    <!-- generated:routes -->
    ...anything here is replaced...
    <!-- /generated:routes -->

Everything outside the markers is left exactly as it was found, so the overview
and usage sections survive regeneration. ``--check`` regenerates in memory and
reports what would change, which is how the test suite refuses to let the docs
fall behind.

Run it with settings that enable every app, or the apps that are off will be
documented as though they do not exist:

    DJANGO_SETTINGS_MODULE=config.settings.test python manage.py authdocs
"""

import re
from argparse import ArgumentParser
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from django.apps import apps
from django.conf import settings
from django.contrib import admin
from django.core.management.base import BaseCommand, CommandError

from infrastructure.common.admin import RevocableAdmin
from infrastructure.common.appsettings import AppSettings
from infrastructure.common.registry import load_api_registry

SECTIONS = ("routes", "models", "admin", "settings")
# Apps that are this project's own rather than one of the auth/oauth families,
# and still get a page: the foundation every project carries, the accounts it
# resolves to, and the optional feature apps.
PROJECT_LABELS = frozenset({"accounts", "common", "cms", "notifications", "shop", "support"})
NONE = "_None._"


@dataclass(frozen=True)
class DocumentedApp:
    """One app, and where its documentation lives."""

    label: str
    spec: AppSettings | None
    prefix: str
    path: Path


def _marker(section: str) -> tuple[str, str]:
    return f"<!-- generated:{section} -->", f"<!-- /generated:{section} -->"


def _router_owners() -> dict[str, str]:
    """Map an app label to the URL prefix its router is mounted at.

    Every way this project mounts a router is read, not just the auth ones: the
    settings lists, and the declarative registry that feature APIs are added
    through. An app whose routes are documented from one source and mounted from
    another is exactly the drift these pages exist to prevent.
    """
    owners: dict[str, str] = {}
    routes: list[dict[str, Any]] = []
    # Every settings list this project mounts routers from, found by the naming
    # convention rather than by name. Listing them individually is how the next
    # optional app gets a page that says it publishes no routes.
    for name in dir(settings):
        if name.endswith("_ROUTERS"):
            routes.extend(dict(route) for route in getattr(settings, name))
    for configuration in load_api_registry().values():
        routes.extend(dict(route) for route in configuration["routes"])

    for route in routes:
        path = str(route["router"])
        # Longest match wins, so `infrastructure.common` beats `infrastructure`
        # and `apps.cms` is found whatever its router module is called.
        owner = max(
            (config for config in apps.get_app_configs() if path.startswith(f"{config.name}.")),
            key=lambda config: len(config.name),
            default=None,
        )
        if owner is not None:
            owners[owner.label] = str(route["prefix"])
    return owners


def _documented_apps(root: Path) -> list[DocumentedApp]:
    """Every app this project defines, in the order the docs list them."""
    owners = _router_owners()
    found = []
    for config in apps.get_app_configs():
        if config.label not in PROJECT_LABELS and not config.label.startswith(("auth_", "oauth_")):
            continue
        family, _, name = config.label.partition("_")
        # A label with no family prefix -- `accounts` -- is a page of its own at
        # the top level rather than one inside a family directory.
        page = root / family / f"{name.replace('_', '-')}.md" if name else root / f"{family}.md"
        spec = getattr(config, "settings_spec", None)
        found.append(
            DocumentedApp(
                label=config.label,
                spec=spec if isinstance(spec, AppSettings) else None,
                prefix=owners.get(config.label, ""),
                path=page,
            )
        )
    return sorted(found, key=lambda app: app.path.as_posix())


def _operations() -> dict[str, set[tuple[str, str, bool, str]]]:
    """Return ``(verb, path, needs auth, summary)`` grouped by URL prefix.

    Read from the OpenAPI schema rather than from the routers, so what is
    documented is what the API actually publishes.

    The infrastructure routers are mounted into *every* API version, so a project
    that adds a second version publishes each of these operations more than once
    -- the same verb, the same version-relative path, the same summary. That is
    one route documented once, not two, which is why the entries are a set: a
    list gave each row back once per version the project happened to declare.
    """
    from config.api import apis

    grouped: dict[str, set[tuple[str, str, bool, str]]] = {}
    for version, api in apis.items():
        schema = api.get_openapi_schema()
        for path, operations in schema["paths"].items():
            relative = path.removeprefix(f"/api/{version}")
            for verb, operation in operations.items():
                grouped.setdefault(relative, set()).add(
                    (
                        verb.upper(),
                        relative,
                        bool(operation.get("security")),
                        str(operation.get("summary", "")),
                    )
                )
    return grouped


def _routes_table(prefix: str) -> str:
    if not prefix:
        return "_This app publishes no routes of its own._"
    grouped = _operations()
    rows = [
        entry
        for path, entries in grouped.items()
        if path == prefix or path.startswith(f"{prefix}/")
        for entry in entries
    ]
    if not rows:
        return NONE
    lines = [
        "| Method | Path | Auth | Purpose |",
        "| --- | --- | --- | --- |",
    ]
    for verb, path, secured, summary in sorted(rows, key=lambda row: (row[1], row[0])):
        credential = "Bearer" if secured else "None"
        lines.append(f"| `{verb}` | `/api/v1{path}` | {credential} | {summary} |")
    return "\n".join(lines)


def _field_type(field: Any) -> str:
    return field.get_internal_type().replace("Field", "")


def _models_table(label: str) -> str:
    config = apps.get_app_config(label)
    models = list(config.get_models())
    if not models:
        return "_This app defines no models of its own._"
    blocks = []
    for model in sorted(models, key=lambda model: model.__name__):
        summary = (model.__doc__ or "").strip().split("\n")[0]
        blocks.append(f"#### `{model.__name__}`\n")
        if summary and not summary.startswith(model.__name__):
            blocks.append(f"{summary}\n")
        lines = [
            "| Field | Type | Notes |",
            "| --- | --- | --- |",
        ]
        for field in model._meta.fields:
            notes = []
            if field.primary_key:
                notes.append("primary key")
            if getattr(field, "unique", False) and not field.primary_key:
                notes.append("unique")
            if field.is_relation and field.related_model is not None:
                notes.append(f"→ `{field.related_model._meta.label}`")
            if not field.editable:
                notes.append("not editable")
            if field.null:
                notes.append("nullable")
            lines.append(f"| `{field.name}` | {_field_type(field)} | {', '.join(notes)} |")
        blocks.append("\n".join(lines) + "\n")
    return "\n".join(blocks).rstrip()


def _refuses_editing(model_admin: Any) -> bool:
    """Whether this admin is read-only, asked of the admin rather than of its base class.

    An app meant to be copied into another project cannot inherit this project's
    ``ReadOnlyAdmin`` -- that base imports Unfold directly -- so a check by
    ``isinstance`` would quietly report such an app's audit table as editable.

    It is a declared attribute rather than a call to ``has_add_permission``,
    because those methods answer for a *caller* and are free to consult the
    database to do it: a singleton admin refuses a second row by counting the
    rows. Documentation is generated without a database, and a table that read
    differently depending on whether one was open would be worse than one that
    is occasionally out of date.
    """
    return bool(getattr(model_admin, "read_only_admin", False))


def _admin_notes(label: str) -> str:
    config = apps.get_app_config(label)
    models = [model for model in config.get_models() if model in admin.site._registry]
    if not models:
        return "_This app registers nothing in the admin._"
    lines = [
        "| Model | Editable | Actions | Columns |",
        "| --- | --- | --- | --- |",
    ]
    for model in sorted(models, key=lambda model: model.__name__):
        model_admin = admin.site._registry[model]
        if isinstance(model_admin, RevocableAdmin):
            editable = "No — revocable only"
        elif _refuses_editing(model_admin):
            editable = "No — read-only"
        else:
            editable = "Yes"
        actions = ", ".join(f"`{name}`" for name in (model_admin.actions or ())) or "—"
        columns = ", ".join(f"`{name}`" for name in (model_admin.list_display or ()))
        lines.append(f"| `{model.__name__}` | {editable} | {actions} | {columns} |")
    return "\n".join(lines)


def _settings_table(app: "DocumentedApp") -> str:
    """The app's settings contract, however it chose to declare one.

    An app that is part of this project's infrastructure declares an
    :class:`AppSettings` and gets its requirements validated by a system check
    as well as documented here. An app meant to be copied elsewhere cannot
    import that class without dragging the project with it, so it may instead
    carry ``settings_docs``: rows of plain strings, which document but do not
    validate. Both end up in the same table.
    """
    spec = app.spec
    if spec is None or not spec.requirements:
        rows = getattr(apps.get_app_config(app.label), "settings_docs", ())
        if not rows:
            return "_This app requires no settings of its own._"
        lines = [
            "| Environment variable | Required | Purpose |",
            "| --- | --- | --- |",
        ]
        lines.extend(
            f"| `{variable}` | {level} | {purpose}. |" for variable, level, purpose in rows
        )
        return "\n".join(lines)
    lines = [
        "| Environment variable | Required | Purpose |",
        "| --- | --- | --- |",
    ]
    for requirement in spec.requirements:
        if requirement.required:
            level = "**Yes**"
        elif requirement.recommended:
            level = "Recommended"
        else:
            level = "Optional"
        # A one-sided bound is written as one, not as a range with a `None` in
        # it: "Range 0–None" is worse than saying nothing about the top.
        bounds = ""
        if requirement.minimum is not None and requirement.maximum is not None:
            bounds = f" Range {requirement.minimum}–{requirement.maximum}."
        elif requirement.minimum is not None:
            bounds = f" {requirement.minimum} or more."
        elif requirement.maximum is not None:
            bounds = f" {requirement.maximum} or less."
        lines.append(f"| `{requirement.env}` | {level} | {requirement.purpose}.{bounds} |")
    return "\n".join(lines)


def _scaffold(app: DocumentedApp) -> str:
    """A fresh page, with the prose left for a person to write."""
    title = app.spec.title if app.spec else app.label
    summary = app.spec.summary if app.spec else ""
    body = [f"# {title}\n", f"{summary}\n" if summary else "", "## Routes\n"]
    for section, heading in (
        ("routes", None),
        ("models", "## Models\n"),
        ("admin", "## Admin\n"),
        ("settings", "## Setup\n"),
    ):
        if heading:
            body.append(heading)
        opening, closing = _marker(section)
        body.append(f"{opening}\n{closing}\n")
    body.append("## Usage\n")
    return "\n".join(part for part in body if part)


def _render(app: DocumentedApp, text: str) -> str:
    """Replace each generated block in ``text``, leaving everything else alone."""
    generated = {
        "routes": _routes_table(app.prefix),
        "models": _models_table(app.label),
        "admin": _admin_notes(app.label),
        "settings": _settings_table(app),
    }
    for section, content in generated.items():
        opening, closing = _marker(section)
        pattern = re.compile(
            f"{re.escape(opening)}.*?{re.escape(closing)}",
            re.DOTALL,
        )
        if not pattern.search(text):
            raise CommandError(
                f"{app.path} has no '{section}' block. "
                f"Add {opening} ... {closing} where it belongs."
            )
        text = pattern.sub(f"{opening}\n{content}\n{closing}", text)
    return text


class Command(BaseCommand):
    help = "Generate the reference sections of the per-app documentation."

    def add_arguments(self, parser: ArgumentParser) -> None:
        parser.add_argument(
            "--check",
            action="store_true",
            help="Report what would change and exit non-zero, without writing.",
        )
        parser.add_argument(
            "--docs-root",
            type=Path,
            default=Path(settings.BASE_DIR) / "docs",
            help="Where the documentation tree lives.",
        )

    def handle(self, *args: Any, **options: Any) -> None:
        root: Path = options["docs_root"]
        check: bool = options["check"]
        stale: list[str] = []
        written = 0

        for app in _documented_apps(root):
            existing = app.path.read_text(encoding="utf-8") if app.path.exists() else ""
            rendered = _render(app, existing or _scaffold(app))
            if rendered == existing:
                continue
            relative = app.path.relative_to(root.parent)
            if check:
                stale.append(str(relative))
                continue
            app.path.parent.mkdir(parents=True, exist_ok=True)
            app.path.write_text(rendered, encoding="utf-8")
            written += 1
            self.stdout.write(f"wrote {relative}")

        if check and stale:
            raise CommandError(
                "Documentation is out of date for:\n  "
                + "\n  ".join(stale)
                + "\nRun: python manage.py authdocs"
            )
        if check:
            self.stdout.write(self.style.SUCCESS("Documentation is up to date."))
        else:
            self.stdout.write(self.style.SUCCESS(f"{written} file(s) updated."))


def documented_apps(root: Path) -> list[DocumentedApp]:
    """Public name for the app inventory, for tests to walk."""
    return _documented_apps(root)
