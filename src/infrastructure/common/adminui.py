"""What the admin looks like -- assembled from the apps, not written out here.

Unfold themes the admin, and everything it themes is configured through one
``UNFOLD`` dictionary. The parts of that dictionary which have to *think* live
here, and what they think about is: **which apps did this deployment actually
install?**

That question used to be answered by a list in this module -- a function per app
building its sidebar group, another per app counting its numbers, and a template
with a hard-coded block for each. It worked, and it was wrong in the way that
only shows up later: the support desk shipped with a ``desk_numbers`` function
that nothing called, because adding an app meant remembering to edit three
project files it has no other reason to touch. An app that is meant to be lifted
out cannot own half of its own admin.

So this module no longer knows any app. It knows a **protocol**, and walks the
installed apps looking for it -- the same convention ``config/graph.py`` uses to
assemble the schema and ``config/grpc.py`` to register servicers:

    <app package>/adminui.py

        NAVIGATION_ORDER = 30
        def navigation(request) -> group | [group] | None: ...

        DASHBOARD_ORDER = 30
        def dashboard(request) -> section | None: ...

Both are optional, both are asked per request, and both may return ``None`` for
"nothing to show this person" -- which is how permissions are applied: a
contributor checks what its reader may see and simply offers less. Nothing here
filters afterwards, because a filter here would need to know what each card
means.

**Groups merge by title.** Several apps contribute to one heading -- every token
mode puts something under Credentials -- so a group is addressed by its title
rather than owned by one app, and the items of same-titled groups are
concatenated in order. That is what lets a deployment running only sliding
tokens see a Credentials group with one item in it, and a deployment running
none see no such group at all.

The rendering end of this is ``src/templates/admin/index.html``, which draws
whatever it is handed and knows no app either.
"""

from collections.abc import Callable, Iterable
from importlib import import_module
from types import ModuleType
from typing import Any, TypedDict, cast

from django.apps import apps
from django.http import HttpRequest
from django.urls import NoReverseMatch, reverse_lazy

#: Where an app declares its admin contribution, relative to its package.
CONTRIBUTION_MODULE = "adminui"

#: Where a contribution sits when it does not say. Deliberately large, so an app
#: that has no opinion sorts after the ones that do rather than in front of them.
DEFAULT_ORDER = 100


class Card(TypedDict, total=False):
    """One number on the dashboard, and what somebody does about it.

    ``value`` is the number; ``label`` says what it counts; ``hint`` is the line
    underneath that turns a count into a sentence -- "nothing is awaiting
    payment" rather than a bare zero. ``tone`` colours the value where it means
    something is wrong: nothing, ``good``, ``warn`` or ``bad``.
    """

    label: str
    value: Any
    hint: str
    icon: str
    link: Any
    tone: str


class Panel(TypedDict, total=False):
    """A wider block: a few figures, some bars, a short list, or any of the three.

    One shape rather than one per kind, because the template then has one block
    rather than a chain of ``{% if %}``, and an app that wants figures over a
    list does not need a new panel type adding to this module first.
    """

    title: str
    figures: list[dict[str, Any]]
    bars: list[dict[str, Any]]
    rows: list[dict[str, Any]]
    note: str
    link: Any
    link_label: str
    wide: bool


class Section(TypedDict, total=False):
    """One app's whole contribution to the front page."""

    title: str
    cards: list[Card]
    panels: list[Panel]


class Group(TypedDict, total=False):
    """One heading in the sidebar, and the links under it.

    ``order`` is where the *heading* sits, which is not the same question as
    where its contributor sits: Credentials belongs above Audit however many
    apps fill either, and whichever of them happens to be installed should not
    decide that. A group with no order takes its contributor's.
    """

    title: str
    order: int
    separator: bool
    collapsible: bool
    items: list[dict[str, Any]]


# -- what a contributor is given -------------------------------------------


def changelist(app_label: str, model: str) -> Any:
    """The admin list page for one model, reversed lazily.

    Lazy because navigation is built while the URL configuration may still be
    loading, and because an item nobody may see is never resolved at all.
    """
    return reverse_lazy(f"admin:{app_label}_{model}_changelist")


def admin_url(name: str, *args: Any) -> str:
    """A named admin route's address, or ``""`` where this project has none.

    For the screens an app adds itself -- the CMS content editor, the support
    desk -- which a project can perfectly well install the app without keeping.
    A missing one should cost that link, not the whole page.
    """
    try:
        return str(reverse_lazy(name, args=args))
    except NoReverseMatch:  # pragma: no cover - only where an admin was replaced
        return ""


def may(request: HttpRequest, *permissions: str) -> bool:
    """Whether this account holds any of these permissions.

    Superusers pass everything, which is Django's own rule. Offered to
    contributors so that "may this person see this card" is answered the same
    way everywhere, rather than five apps each writing their own version of the
    superuser exception and one of them forgetting it.
    """
    user = request.user
    return bool(user.is_superuser or any(user.has_perm(name) for name in permissions))


def item(title: str, icon: str, link: Any, *permissions: str) -> dict[str, Any]:
    """One sidebar link, with the permission check Unfold applies per request."""
    entry: dict[str, Any] = {"title": title, "icon": icon, "link": link}
    if permissions:
        entry["permission"] = lambda request: may(request, *permissions)
    return entry


def card(
    label: str,
    value: Any,
    *,
    hint: str = "",
    icon: str = "",
    link: Any = None,
    tone: str = "",
) -> Card:
    """One dashboard number. A function so a missing key is a type error here."""
    return Card(label=label, value=value, hint=hint, icon=icon, link=link, tone=tone)


# -- finding them -----------------------------------------------------------


def _contribution_module(app_name: str) -> ModuleType | None:
    """Import one app's ``adminui``, or ``None`` if it publishes no admin UI.

    A missing module is an app that contributes nothing. An ImportError raised
    from *inside* the module is that app's bug and is re-raised, rather than
    quietly becoming a dashboard with a section missing -- which is exactly the
    failure this whole module exists to stop.
    """
    module_name = f"{app_name}.{CONTRIBUTION_MODULE}"
    if module_name == __name__:
        return None
    try:
        return import_module(module_name)
    except ModuleNotFoundError as error:
        if error.name == module_name:
            return None
        raise


def contributions(attribute: str) -> list[tuple[int, str, Callable[..., Any]]]:
    """``(order, app label, callable)`` for every installed app that declares one.

    Sorted, and by app label after the order, so two apps that both said 30 come
    out in the same sequence on every request rather than in whatever order the
    app registry happens to hold them.
    """
    found: list[tuple[int, str, Callable[..., Any]]] = []
    for config in apps.get_app_configs():
        module = _contribution_module(config.name)
        if module is None:
            continue
        function = getattr(module, attribute, None)
        if not callable(function):
            continue
        order = getattr(module, f"{attribute.upper()}_ORDER", DEFAULT_ORDER)
        found.append((int(order), config.label, function))
    return sorted(found, key=lambda row: (row[0], row[1]))


def _groups_from(result: Any) -> Iterable[Group]:
    """A contributor may return one group, several, or nothing at all."""
    if result is None:
        return ()
    if isinstance(result, dict):
        return (cast(Group, result),)
    return cast(Iterable[Group], result)


def environment_badge(request: HttpRequest) -> list[str] | None:
    """The label beside the account menu, so nobody edits production by mistake.

    Debug is the signal rather than a setting of its own: it is already the line
    between a machine somebody is developing on and one real users can reach.
    """
    from django.conf import settings

    if settings.DEBUG:
        return ["Development", "warning"]
    return None


def _overview_group() -> Group:
    """The two links that are the project's own rather than any app's."""
    return Group(
        title="Overview",
        order=0,
        separator=False,
        collapsible=False,
        items=[
            {"title": "Dashboard", "icon": "dashboard", "link": reverse_lazy("admin:index")},
            {"title": "API documentation", "icon": "api", "link": reverse_lazy("api-docs")},
        ],
    )


def sidebar_navigation(request: HttpRequest) -> list[Group]:
    """Every group the installed apps can fill, in the order they are used.

    Unfold resolves this per request, which is what lets it be a function of the
    installed apps rather than a list written before they were chosen.

    Groups are merged by title and empty ones are dropped, so an app that
    offered this reader nothing leaves no heading behind -- a heading with no
    links under it is worse than no heading, because it reads as something
    broken rather than as something absent.
    """
    merged: dict[str, Group] = {"Overview": _overview_group()}
    for order, _, contribute in contributions("navigation"):
        for group in _groups_from(contribute(request)):
            title = group.get("title", "")
            existing = merged.get(title)
            if existing is None:
                merged[title] = Group(
                    title=title,
                    order=group.get("order", order),
                    separator=group.get("separator", False),
                    collapsible=group.get("collapsible", False),
                    items=list(group.get("items") or []),
                )
                continue
            existing["items"] = [*existing.get("items", []), *(group.get("items") or [])]
            # The earliest opinion wins, so a heading does not move down the
            # sidebar because a second app also fills it.
            existing["order"] = min(existing.get("order", order), group.get("order", order))
    filled = [group for group in merged.values() if group.get("items")]
    return sorted(filled, key=lambda group: group.get("order", DEFAULT_ORDER))


def dashboard(request: HttpRequest, context: dict[str, Any]) -> dict[str, Any]:
    """Fill the front page with whatever the installed apps have to say.

    Every section is an app's own answer to "what should somebody do next", and
    an app that has nothing to tell *this* reader returns nothing rather than a
    row of cards that answer 403. The template draws what it is handed.
    """
    sections: list[Section] = []
    for _, _, contribute in contributions("dashboard"):
        section = contribute(request)
        if section and (section.get("cards") or section.get("panels")):
            sections.append(section)
    context["admin_sections"] = sections
    return context
