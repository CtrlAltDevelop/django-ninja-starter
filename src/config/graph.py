"""Assemble the project's GraphQL schema from whatever apps are installed.

The REST document is built from a registry of routers; this is built the same
way, only by convention rather than by a list. Every app that publishes GraphQL
puts a ``graph/schema.py`` beside its ``rest/`` package with a ``Query`` and, if
it has anything to change, a ``Mutation``. An app that is not installed
contributes neither -- which is what keeps a deployment's schema to the features
it actually turned on, exactly as its OpenAPI document is.

Strawberry merges contributions by inheritance, which means two apps declaring a
resolver under the same *attribute* name would leave one field where two were
meant -- silently. So the merge checks for that and refuses instead.
"""

from importlib import import_module
from types import ModuleType

import strawberry
from django.apps import apps
from django.core.exceptions import ImproperlyConfigured


def _app_graph_module(app_name: str) -> ModuleType | None:
    """Import one app's ``graph.schema``, or return ``None`` if it has none.

    A missing package is an app that does not publish GraphQL. An import error
    raised from *inside* the package is a bug in that app, and is re-raised
    rather than quietly turning into a schema with fields missing.
    """
    module_name = f"{app_name}.graph.schema"
    try:
        return import_module(module_name)
    except ModuleNotFoundError as error:
        if error.name in {module_name, f"{app_name}.graph"}:
            return None
        raise


def graph_contributions(attribute: str) -> list[tuple[str, type]]:
    """Collect every installed app's ``Query`` or ``Mutation`` class, with its app.

    The app label travels with the class because several apps build their
    contribution from the same factory, and a collision message naming the
    factory would not tell anybody which two apps to look at.
    """
    contributions: list[tuple[str, type]] = []
    for config in apps.get_app_configs():
        module = _app_graph_module(config.name)
        part = getattr(module, attribute, None) if module else None
        if part is not None:
            contributions.append((config.label, part))
    return contributions


@strawberry.type
class BaseQuery:
    """The one field a schema is guaranteed to have.

    GraphQL requires a root query with at least one field, and a project can be
    generated with every optional app turned off. This is what it answers with
    before anything else is installed.
    """

    @strawberry.field(description="Whether the GraphQL endpoint is serving.")
    def ok(self) -> bool:
        return True


def _merge(name: str, contributions: list[tuple[str, type]]) -> type:
    """Combine app contributions into one root type, refusing to lose a field.

    Inheritance resolves a repeated attribute name silently, and the field that
    loses simply is not in the schema. A deployment is better served by failing
    to start than by publishing a document with an endpoint quietly missing from
    it.
    """
    seen: dict[str, str] = {}
    for label, contribution in contributions:
        for attribute in vars(contribution):
            if attribute.startswith("_"):
                continue
            owner = seen.setdefault(attribute, label)
            if owner != label:
                raise ImproperlyConfigured(
                    f"GraphQL {name} field {attribute!r} is declared by both "
                    f"the {owner} and {label} apps."
                )
    return strawberry.type(type(name, tuple(part for _, part in contributions), {}))


def build_schema() -> strawberry.Schema:
    """Build the root schema for the apps this deployment installed."""
    queries = graph_contributions("Query")
    mutations = graph_contributions("Mutation")

    query = _merge("Query", [*queries, ("config", BaseQuery)])
    mutation = _merge("Mutation", mutations) if mutations else None
    return strawberry.Schema(query=query, mutation=mutation)


schema = build_schema()
