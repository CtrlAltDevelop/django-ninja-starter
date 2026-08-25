"""The vocabulary every token mode shares: what a credential is, and how to sign one.

Each of the three modes -- sliding, session, rotation -- keeps its own tables and
its own idea of what refreshing means, but they all hand the client the same
shape and all read the same request headers. Those parts live here so that the
mode apps and the login flows can both use them without importing each other.

Deliberately model-free. A mode's models are reached through the app registry by
label, so enabling ``sliding`` never drags ``rotation``'s tables into the schema.
"""

from dataclasses import dataclass
from datetime import timedelta
from typing import Any, Final

from django.apps import apps
from django.core.exceptions import ImproperlyConfigured
from django.http import HttpRequest

from infrastructure.common.app_labels import app_installed
from infrastructure.oauth.core import jwt_tokens

TOKEN_MODE_APPS: Final = {
    "sliding": "oauth_sliding",
    "session": "oauth_session",
    "rotation": "oauth_rotation",
}
BEARER: Final = "bearer"


@dataclass(frozen=True)
class IssuedCredentials:
    """What a successful login, or a successful refresh, hands back to the client."""

    token_type: str
    access_token: str = ""
    refresh_token: str = ""
    expires_in: int | None = None
    session_id: str = ""


def client_ip(request: HttpRequest) -> str | None:
    forwarded = request.META.get("HTTP_X_FORWARDED_FOR", "")
    return (
        forwarded.split(",", 1)[0].strip() if forwarded else request.META.get("REMOTE_ADDR")
    ) or None


def user_agent(request: HttpRequest) -> str:
    return request.META.get("HTTP_USER_AGENT", "")


def bearer_token(request: HttpRequest) -> str:
    header = request.META.get("HTTP_AUTHORIZATION", "")
    scheme, _, value = header.partition(" ")
    return value.strip() if scheme.lower() == BEARER else ""


def token_model(mode: str, name: str) -> Any:
    """Return a mode's model, insisting the mode's app is actually installed.

    Without this the failure would be a token silently never issued, which is a
    far harder thing to diagnose than a configuration error at the call site.
    """
    app_label = TOKEN_MODE_APPS[mode]
    if not app_installed(app_label):
        raise ImproperlyConfigured(
            f"DJANGO_AUTH_TOKEN_MODE={mode} needs DJANGO_OAUTH_MODE to enable {app_label}."
        )
    return apps.get_model(app_label, name)


def credential_handle(token: str, *, token_type: str) -> str:
    """Return the row handle inside a token, or an empty string if it is not ours."""
    try:
        return jwt_tokens.decode(token, token_type=token_type).handle
    except jwt_tokens.JwtError:
        return ""


def sign_single(
    user: Any,
    *,
    mode: str,
    session_id: str,
    methods: list[str],
    handle: str,
    signature_lifetime: timedelta,
    expires_in: int,
) -> IssuedCredentials:
    """Sign a mode that has one token doing both jobs.

    ``signature_lifetime`` and ``expires_in`` come apart on purpose: a sliding
    token's signature has to cover the absolute lifetime while the value the
    client is told is the current idle window.
    """
    access_token, _ = jwt_tokens.mint(
        subject=str(user.pk),
        handle=handle,
        token_type=jwt_tokens.ACCESS,
        lifetime=signature_lifetime,
        mode=mode,
        session_id=session_id,
        methods=methods,
    )
    return IssuedCredentials(
        token_type=BEARER,
        access_token=access_token,
        expires_in=expires_in,
        session_id=session_id,
    )


def sign_pair(
    user: Any,
    *,
    mode: str,
    session_id: str,
    methods: list[str],
    access_handle: str,
    refresh_handle: str,
    access_lifetime: timedelta,
    refresh_lifetime: timedelta,
) -> IssuedCredentials:
    """Sign a stored access/refresh handle pair as a matching pair of tokens."""
    subject = str(user.pk)
    access_token, _ = jwt_tokens.mint(
        subject=subject,
        handle=access_handle,
        token_type=jwt_tokens.ACCESS,
        lifetime=access_lifetime,
        mode=mode,
        session_id=session_id,
        methods=methods,
    )
    refresh_token, _ = jwt_tokens.mint(
        subject=subject,
        handle=refresh_handle,
        token_type=jwt_tokens.REFRESH,
        lifetime=refresh_lifetime,
        mode=mode,
        session_id=session_id,
        methods=methods,
    )
    return IssuedCredentials(
        token_type=BEARER,
        access_token=access_token,
        refresh_token=refresh_token,
        expires_in=int(access_lifetime.total_seconds()),
        session_id=session_id,
    )
