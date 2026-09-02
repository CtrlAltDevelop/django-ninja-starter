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
from typing import Any

from django.http import HttpRequest, HttpResponse
from django.shortcuts import render
from ninja import NinjaAPI
from ninja.openapi.docs import Swagger


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
            {"swagger_settings": json.dumps(self.settings, indent=1), "api": api},
        )
