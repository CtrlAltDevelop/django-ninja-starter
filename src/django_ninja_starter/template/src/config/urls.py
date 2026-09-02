from django.contrib import admin
from django.http import HttpRequest, HttpResponse
from django.urls import path
from ninja.openapi.docs import Redoc

from config.api import apis


def api_docs(request: HttpRequest) -> HttpResponse:
    """Render Swagger with all registered API versions in its selector."""
    default_api = next(iter(apis.values()))
    return default_api.docs.render_page(request, default_api)


def api_redoc(request: HttpRequest) -> HttpResponse:
    """Render the same versioned REST schema with ReDoc."""
    default_api = next(iter(apis.values()))
    return Redoc().render_page(request, default_api)


urlpatterns = [
    path("admin/", admin.site.urls),
    path("api/docs", api_docs, name="api-docs"),
    path("api/redoc", api_redoc, name="api-redoc"),
    *(path(f"api/{version}/", api.urls) for version, api in apis.items()),
]
