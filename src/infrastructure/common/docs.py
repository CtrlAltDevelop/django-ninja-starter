"""Swagger UI, with the version selector actually wired up.

Django Ninja's own docs page renders one API. This project publishes several --
one per registered version -- so its Swagger settings carry a ``urls`` list and
a ``urls.primaryName``, which is how Swagger UI is told to offer a selector.

Those two settings are read by the *topbar*, and the topbar lives in Swagger
UI's standalone preset: a second script, and a layout by name. Django Ninja's
CDN page loads neither. The result is a page that loads, finds a ``urls`` list
nothing consumes, never fetches a document, and renders "No API definition
provided." -- with a 200 in the log and no error anywhere to explain it.

So this subclass renders a template of our own that loads the standalone preset
alongside the bundle and asks for ``StandaloneLayout``. Everything else is Django
Ninja's page. The alternative -- dropping ``urls`` -- would render fine and lose
the selector, which is the feature.
"""

import json
from collections.abc import Iterable
from typing import Any
from urllib.parse import quote

from django.http import HttpRequest, HttpResponse
from django.shortcuts import render
from django.urls import NoReverseMatch, reverse
from ninja import NinjaAPI
from ninja.openapi.docs import Swagger


def api_tags(routes: Iterable[dict[str, Any]]) -> list[dict[str, str]]:
    """Describe every attached router's tag, for the document's ``tags`` list.

    Swagger UI needs no help *grouping* operations -- each router is attached
    under a tag, so the groups exist either way. What it cannot invent is the
    two things this list adds: a sentence under each group heading saying what
    the group is for, and an order.

    Undeclared, the groups come out in whatever order the paths happened to be
    built in, and a reader meets fifteen collapsed accordions labelled only by
    name. Declared, they appear in the order the routers were attached -- which
    is the order the documentation teaches them in -- each with a line of its
    own.

    Tags are emitted once each, in first-attachment order, so the two routers
    that share ``Auth - Token`` are one group rather than a duplicate heading.
    """
    described: dict[str, str] = {}
    for route in routes:
        described.setdefault(str(route["tag"]), str(route.get("description", "")))
    return [
        {"name": name, "description": description} if description else {"name": name}
        for name, description in described.items()
    ]


def session_status(request: HttpRequest) -> dict[str, Any]:
    """What the page can say about its reader before it asks the server anything.

    The page trades an admin session for a bearer token on load (see the template
    for how, and ``oauth.core.exchange`` for what it calls). Every refusal that
    bridge can return used to be a silent no-op, which left one page appearance
    covering three different situations: authorised, declined, and failed.

    Django already knows which of them applies before the page is sent -- who is
    signed in, and whether they are staff -- so it is rendered into the banner
    here. That makes the first paint truthful rather than optimistic, and it lets
    the two states that can only end in a refusal skip the request entirely,
    instead of putting a guaranteed 401 in the log for every anonymous reader.

    A page rendered outside a request cycle -- no auth middleware, no ``user`` --
    reads as signed out, which is the honest answer for a reader the server
    cannot identify.
    """
    user = getattr(request, "user", None)
    if user is not None and not user.is_authenticated:
        # An AnonymousUser is a reader the page knows nothing about, so it is
        # folded into the same case as no user attribute at all.
        user = None
    try:
        # Reversed rather than hardcoded: a project that mounts the admin
        # somewhere other than /admin/ still gets a link that goes there, and one
        # that drops the admin entirely gets no link rather than a broken one.
        login_url = f"{reverse('admin:login')}?next={quote(request.get_full_path())}"
    except NoReverseMatch:
        login_url = ""
    return {
        "session_signed_in": user is not None,
        "session_is_staff": bool(user is not None and user.is_staff),
        "session_username": user.get_username() if user is not None else "",
        "admin_login_url": login_url,
    }


class VersionedSwagger(Swagger):
    """Swagger UI able to switch between every registered API version."""

    template = "common/swagger.html"

    def render_page(self, request: HttpRequest, api: NinjaAPI, **kwargs: Any) -> HttpResponse:
        # The document this page opens on. Django Ninja sets the same key, and
        # Swagger UI falls back to it when a selector has no primary name.
        self.settings["url"] = self.get_openapi_url(api, kwargs)
        return render(
            request,
            self.template,
            {
                "swagger_settings": json.dumps(self.settings, indent=1),
                "api": api,
                **session_status(request),
            },
        )
