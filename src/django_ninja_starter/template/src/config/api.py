"""Build every registered version of the project's Django Ninja API."""

from django.conf import settings
from ninja import NinjaAPI

from infrastructure.common.docs import VersionedSwagger, api_tags
from infrastructure.common.errors import register_error_handlers
from infrastructure.common.registry import api_version_number, load_api_registry
from infrastructure.common.responses import EnvelopeAPI, EnvelopeRenderer
from infrastructure.common.throttling import default_throttles, login_throttles


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
        # Every router this version publishes, in the order the documentation
        # teaches them: the project's own feature APIs first, then the account
        # they belong to, then the ways in, then the content and notification
        # apps, then the shop. Swagger reads the order off the tag list
        # built from it.
        # The doors that take a secret and say whether it was right: every login
        # method, every token endpoint and every social callback. Held apart
        # from the rest only so they can be rate-limited harder, as a group
        # rather than one decorated endpoint at a time -- a login method added
        # later is then throttled by having been registered, which is the only
        # way this stays true.
        credential_routes = [
            *settings.AUTH_METHOD_ROUTERS,
            *settings.AUTH_TOKEN_ROUTERS,
            *settings.OAUTH_PROVIDER_ROUTERS,
        ]
        routes = [
            *configuration["routes"],
            *settings.ACCOUNT_ROUTERS,
            *credential_routes,
            *settings.CMS_ROUTERS,
            *settings.NOTIFICATIONS_ROUTERS,
            *settings.SHOP_ROUTERS,
            *settings.SUPPORT_ROUTERS,
            *settings.WALLET_ROUTERS,
        ]
        guarded = {id(route["router"]) for route in credential_routes}
        api = EnvelopeAPI(
            title="{{ project_title }} API",
            version=api_version_number(version),
            urls_namespace=f"api-{version.replace('.', '-')}",
            # Every route, rather than the ones somebody remembered to decorate.
            # A rate limit applied per endpoint is a rate limit missing from the
            # endpoint added next week, and the endpoint added next week is the
            # one nobody has load-tested. An operation that needs something
            # tighter overrides this with its own `throttle=`.
            throttle=default_throttles(),
            docs=VersionedSwagger(
                settings={
                    "persistAuthorization": True,
                    # The selector, and the layout that renders it. Without
                    # StandaloneLayout nothing reads `urls` and the page loads
                    # empty.
                    "layout": "StandaloneLayout",
                    "urls": swagger_urls,
                    "urls.primaryName": version,
                }
            ),
            docs_url="/docs",
            renderer=EnvelopeRenderer(),
            # What the reader sees before a single operation: one described
            # group per attached router, in the order above.
            openapi_extra={"tags": api_tags(routes)},
        )
        for route in routes:
            api.add_router(
                route["prefix"],
                route["router"],
                tags=[route["tag"]],
                throttle=(
                    login_throttles() if id(route["router"]) in guarded else default_throttles()
                ),
            )
        register_error_handlers(api)
        if settings.AUTH_INSTALLED_APPS:
            register_auth_exception_handlers(api)
        apis[version] = api

    return apis


apis = build_apis()
