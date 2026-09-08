"""The complete authentication route surface, and the settings that mount it.

The per-app suites prove each route *behaves*; this one proves the set of routes
is exactly what is intended. A router silently dropped from ``AUTH_METHOD_ROUTERS``
would otherwise turn every test for it into a vacuous 404.
"""

import pytest
from django.conf import settings
from django.test import Client

from config.api import apis

PASSWORD_ROUTES = {
    ("POST", "/auth/password/signup"),
    ("POST", "/auth/password/login"),
    ("POST", "/auth/password/logout"),
    ("POST", "/auth/password/forgot"),
    ("POST", "/auth/password/reset"),
    ("POST", "/auth/password/change"),
}
EMAIL_CODE_ROUTES = {
    ("POST", "/auth/email-code/signup/start"),
    ("POST", "/auth/email-code/signup/verify"),
    ("POST", "/auth/email-code/login/start"),
    ("POST", "/auth/email-code/login/verify"),
    ("POST", "/auth/email-code/logout"),
}
SMS_CODE_ROUTES = {
    ("POST", "/auth/sms-code/signup/start"),
    ("POST", "/auth/sms-code/signup/verify"),
    ("POST", "/auth/sms-code/login/start"),
    ("POST", "/auth/sms-code/login/verify"),
    ("POST", "/auth/sms-code/logout"),
}
MAGIC_LINK_ROUTES = {
    ("POST", "/auth/magic-link/signup/start"),
    ("POST", "/auth/magic-link/login/start"),
    ("POST", "/auth/magic-link/verify"),
    ("POST", "/auth/magic-link/logout"),
}
TWO_FACTOR_ROUTES = {
    ("POST", "/auth/2fa/challenge"),
    ("POST", "/auth/2fa/verify"),
    ("GET", "/auth/2fa/methods"),
    ("POST", "/auth/2fa/totp/enroll"),
    ("POST", "/auth/2fa/totp/confirm"),
    ("POST", "/auth/2fa/sms/enroll"),
    ("POST", "/auth/2fa/sms/confirm"),
    ("POST", "/auth/2fa/email/enroll"),
    ("POST", "/auth/2fa/email/confirm"),
    ("POST", "/auth/2fa/recovery/generate"),
    ("DELETE", "/auth/2fa/{method}"),
}
TOKEN_ROUTES = {
    ("POST", "/auth/token/exchange"),
    ("POST", "/auth/token/from-session"),
    ("POST", "/auth/token/refresh"),
    ("POST", "/auth/token/revoke"),
    ("GET", "/auth/token/sessions"),
    ("DELETE", "/auth/token/sessions/{session_id}"),
}
ALL_ROUTES = (
    PASSWORD_ROUTES
    | EMAIL_CODE_ROUTES
    | SMS_CODE_ROUTES
    | MAGIC_LINK_ROUTES
    | TWO_FACTOR_ROUTES
    | TOKEN_ROUTES
)


def _registered() -> set[tuple[str, str]]:
    schema = apis["v1"].get_openapi_schema()
    return {
        (verb.upper(), path.removeprefix("/api/v1"))
        for path, operations in schema["paths"].items()
        for verb in operations
        if "/auth/" in path
    }


def test_every_expected_route_is_registered() -> None:
    assert _registered() == ALL_ROUTES


def test_no_undeclared_auth_route_slips_in() -> None:
    """A new endpoint has to be added here, which means it has to be noticed."""
    assert _registered() - ALL_ROUTES == set()


@pytest.mark.parametrize(
    ("group", "method"),
    [
        (PASSWORD_ROUTES, "password"),
        (EMAIL_CODE_ROUTES, "email_code"),
        (SMS_CODE_ROUTES, "sms_code"),
        (MAGIC_LINK_ROUTES, "magic_link"),
    ],
)
def test_each_enabled_method_contributes_its_routes(
    group: set[tuple[str, str]], method: str
) -> None:
    assert method in settings.AUTH_METHODS
    assert group <= _registered()


@pytest.mark.parametrize(("verb", "path"), sorted(ALL_ROUTES))
def test_every_route_is_reachable(db: None, verb: str, path: str) -> None:
    """Every route answers at the URL its own tests use.

    A 404 or 405 here means the endpoint is not mounted where it is documented,
    which no amount of behavioural testing against the same wrong URL would
    reveal. What the handler decides about an empty body is each route's own
    business -- the logout routes are deliberately idempotent and answer 200.
    """
    url = f"/api/v1{path}".replace("{method}", "totp").replace(
        "{session_id}", "00000000-0000-0000-0000-000000000000"
    )
    response = getattr(Client(), verb.lower())(url, {}, content_type="application/json")

    assert response.status_code not in {404, 405}
    assert response.status_code < 500


def test_router_wiring_follows_the_enabled_methods() -> None:
    prefixes = {route["prefix"] for route in settings.AUTH_METHOD_ROUTERS}
    expected = {f"/auth/{method.replace('_', '-')}" for method in settings.AUTH_METHODS}
    if settings.AUTH_SECOND_FACTORS:
        expected.add("/auth/2fa")

    assert prefixes == expected


def test_the_token_router_follows_the_active_mode() -> None:
    """One prefix whichever mode is active, so a client never has to care."""
    routers = settings.AUTH_TOKEN_ROUTERS

    if settings.AUTH_TOKEN_MODE == "none":
        assert routers == []
        return
    assert [route["prefix"] for route in routers] == ["/auth/token"]
    assert routers[0]["router"] == (f"infrastructure.oauth.{settings.AUTH_TOKEN_MODE}.rest.router")


def test_enabled_methods_install_their_apps() -> None:
    installed = set(settings.AUTH_INSTALLED_APPS)

    assert "infrastructure.auth.core.apps.AuthCoreConfig" in installed
    for method in settings.AUTH_METHODS:
        assert settings.AUTH_METHOD_APPS[method] in installed
    if settings.AUTH_SECOND_FACTORS:
        assert "infrastructure.auth.twofactor.apps.AuthTwoFactorConfig" in installed
