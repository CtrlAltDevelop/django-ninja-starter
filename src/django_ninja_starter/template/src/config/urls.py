from django.contrib import admin
from django.http import Http404, HttpRequest, HttpResponse
from django.urls import path
from ninja.openapi.docs import Redoc

from config.api import apis


def api_docs(request: HttpRequest) -> HttpResponse:
    """Render Swagger with all registered API versions in its selector."""
    default_api = next(iter(apis.values()))
    return default_api.docs.render_page(request, default_api)


def api_redoc(request: HttpRequest, version: str = "") -> HttpResponse:
    """Render one registered version of the same schema with ReDoc.

    The reading half of the pair, as Django Ninja ships it. ReDoc renders the
    single document it is handed and has no selector to switch it, so the version
    is in the path; without one it opens on the same document `/api/docs` does.
    """
    version = version or next(iter(apis))
    if version not in apis:
        raise Http404(f"No API version {version!r} is registered.")
    return Redoc().render_page(request, apis[version])


urlpatterns = [
    path("admin/", admin.site.urls),
    path("api/docs", api_docs, name="api-docs"),
    path("api/redoc", api_redoc, name="api-redoc"),
    # Ahead of the version includes: each version's own router owns everything
    # under its prefix, and would answer this path with its 404 rather than one
    # naming the version that is missing.
    path("api/<str:version>/redoc", api_redoc, name="api-version-redoc"),
    *(path(f"api/{version}/", api.urls) for version, api in apis.items()),
]
