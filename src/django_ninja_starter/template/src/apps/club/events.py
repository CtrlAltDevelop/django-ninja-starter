"""What this app can be told about, and how anything tells it.

The question this module answers is the one that decides whether the club app is
usable outside this repository: **how does a mission know that something
happened, in code the club app has never heard of?**

The answer is a registry and one function. An app -- built in or yours --
declares the events it emits::

    from apps.club.events import EventSpec, register

    register(EventSpec(
        key="shop.order.paid",
        label="An order was paid for",
        description="Sent once per order, when payment settles.",
        fields={"total": "Order total in the shop's currency", "items": "How many lines"},
    ))

and then, wherever that actually happens::

    from apps.club import track

    track(user, "shop.order.paid", value=order.total, metadata={"items": 3},
          reference=f"order:{order.pk}")

`track` is the whole integration surface. It does nothing if the club app is not
installed, nothing if the account is in no club, and nothing if no mission is
listening -- so a call site can be added without knowing or caring whether any of
that is true today.

**Why a registry at all**, when `track` could take any string? Because a mission
is configured by an operator in an admin form, and an operator typing
``shop.order.payed`` into a free-text field creates a mission that silently never
fires. The registry turns that into a dropdown and a validation error. It is also
the only honest way to document what a deployment can build missions out of: the
list is generated from what is registered, not from a wiki page.

**Registration is not coupling.** The registry holds strings and descriptions,
never imports, so `apps.club` does not import the shop to know about
``shop.order.paid`` -- the shop announces itself. Which is why the built-in
bridges in :mod:`apps.club.bridges` are each guarded by `apps.is_installed`: a
deployment running the club without the shop registers no shop events, and a
mission that referred to one is refused rather than quietly dead.
"""

from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

#: Keys look like ``<source>.<thing>.<happened>``. Not enforced -- a project may
#: name events whatever it likes -- but every built-in one follows it, and a
#: convention nobody has to look up is worth more than a validator.
KEY_HINT = "source.thing.happened, such as shop.order.paid"


@dataclass(frozen=True)
class EventSpec:
    """One kind of thing that can happen, as the app that emits it describes it.

    ``fields`` is documentation, not a schema: it tells whoever is writing a
    mission's criteria which keys they can expect in the metadata. Validating it
    would make every emitter's payload a breaking change.
    """

    key: str
    label: str
    description: str = ""
    #: What ``value`` means for this event, if it carries one. A purchase might
    #: send its total; a login sends nothing and ``value`` stays 1.
    value_label: str = ""
    fields: dict[str, str] = field(default_factory=dict)
    #: Which app declared it, filled in by :func:`register` when it can tell.
    source: str = ""


_REGISTRY: dict[str, EventSpec] = {}


def register(spec: EventSpec) -> EventSpec:
    """Declare an event. Last registration of a key wins, so a project may override one."""
    _REGISTRY[spec.key] = spec
    return spec


def unregister(key: str) -> None:
    """Forget an event. For tests, and for a deployment disabling a bridge."""
    _REGISTRY.pop(key, None)


def registry() -> dict[str, EventSpec]:
    """Everything registered, keyed by event key."""
    return dict(_REGISTRY)


def known(key: str) -> bool:
    return key in _REGISTRY


def spec_for(key: str) -> EventSpec | None:
    return _REGISTRY.get(key)


def keys() -> list[str]:
    return sorted(_REGISTRY)


def choices() -> list[tuple[str, str]]:
    """The registry as a form's dropdown: an operator picks, rather than types."""
    return [(spec.key, f"{spec.label} ({spec.key})") for spec in sorted_specs()]


def sorted_specs() -> list[EventSpec]:
    return [_REGISTRY[key] for key in sorted(_REGISTRY)]


def load(paths: Iterable[str]) -> None:
    """Import each module named, so that importing it registers what it declares.

    ``DJANGO_CLUB_EVENT_SOURCES`` is a list of dotted module paths, which is how
    an app this starter has never seen gets its events into the registry without
    anything here importing it by name.
    """
    from importlib import import_module

    for path in paths:
        if path.strip():
            import_module(path.strip())


@dataclass(frozen=True)
class Occurrence:
    """One thing that actually happened, on its way to the missions that care.

    ``value`` is the number a mission can compare against -- an order total, a
    deposit amount, a streak length -- and defaults to 1 so that "it happened"
    is the same shape as "it happened, and it was worth this much".
    """

    key: str
    user: Any
    value: float = 1.0
    metadata: dict[str, Any] = field(default_factory=dict)
    #: Idempotency: two occurrences carrying the same reference are the same
    #: event seen twice, and pay once. Empty means the caller cannot promise
    #: that, and the engine falls back to counting every delivery.
    reference: str = ""
