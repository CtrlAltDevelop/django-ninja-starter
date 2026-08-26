"""Build every registered version of the project's Django Ninja API."""

from django.conf import settings
from django.utils.module_loading import import_string
from ninja import NinjaAPI, Swagger

from infrastructure.common.errors import register_error_handlers
from infrastructure.common.registry import api_version_number, load_api_registry


def build_apis() -> dict[str, NinjaAPI]:
    """Create API instances and attach every router from the registry."""
    if settings.AUTH_INSTALLED_APPS:
        from infrastructure.auth.core.errors import register_auth_exception_handlers
    registry = load_api_registry()
    swagger_urls = [
        {"url": f"/api/{version}/openapi.json", "name": version} for version in registry
    ]
    apis: dict[str, NinjaAPI] = {}

    for version, configuration in registry.items():
        api = NinjaAPI(
            title="{{ project_title }} API",
            version=api_version_number(version),
            urls_namespace=f"api-{version.replace('.', '-')}",
            docs=Swagger(
                settings={
                    "persistAuthorization": True,
                    "urls": swagger_urls,
                    "urls.primaryName": version,
                }
            ),
            docs_url="/docs",
        )
        for route in configuration["routes"]:
            api.add_router(
                route["prefix"],
                route["router"],
                tags=[route["tag"]],
            )
        for route in (
            *settings.ACCOUNT_ROUTERS,
            *settings.OAUTH_PROVIDER_ROUTERS,
            *settings.AUTH_METHOD_ROUTERS,
            *settings.AUTH_TOKEN_ROUTERS,
        ):
            api.add_router(
                route["prefix"],
                import_string(route["router"]),
                tags=[route["tag"]],
            )
        register_error_handlers(api)
        if settings.AUTH_INSTALLED_APPS:
            register_auth_exception_handlers(api)
        apis[version] = api

    return apis


apis = build_apis()
