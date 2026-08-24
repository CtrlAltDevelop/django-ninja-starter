"""Django system checks for social OAuth configuration."""

import re
from urllib.parse import urlsplit

from django.conf import settings
from django.core.checks import Error, Warning, register

MICROSOFT_TENANT_PATTERN = re.compile(
    r"^(common|organizations|consumers|[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-"
    r"[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12})$"
)
REQUIRED_PROVIDER_SETTINGS = {
    "google": ("client_id", "client_secret"),
    "apple": ("client_id", "team_id", "key_id", "private_key"),
    "microsoft": ("client_id", "client_secret"),
    "github": ("client_id", "client_secret"),
}


@register()
def check_social_oauth_settings(**kwargs: object) -> list[Error | Warning]:
    messages: list[Error | Warning] = []
    for provider in settings.OAUTH_PROVIDERS:
        configuration = settings.OAUTH_PROVIDER_CONFIG[provider]
        missing = [
            key for key in REQUIRED_PROVIDER_SETTINGS[provider] if not configuration.get(key)
        ]
        if missing:
            messages.append(
                Error(
                    f"{provider.title()} OAuth is enabled but missing: {', '.join(missing)}",
                    id="oauth.E001",
                )
            )
        redirect_uri = str(configuration.get("redirect_uri", ""))
        if not redirect_uri:
            messages.append(
                Warning(
                    f"{provider.title()} OAuth uses a request-derived callback URI",
                    hint="Set the provider's *_OAUTH_REDIRECT_URI to the exact registered URL.",
                    id="oauth.W001",
                )
            )
        elif provider == "apple" and urlsplit(redirect_uri).scheme != "https":
            messages.append(Error("Apple OAuth redirect URI must use HTTPS", id="oauth.E002"))

    microsoft_tenant = str(settings.OAUTH_PROVIDER_CONFIG["microsoft"].get("tenant", ""))
    if "microsoft" in settings.OAUTH_PROVIDERS and not MICROSOFT_TENANT_PATTERN.fullmatch(
        microsoft_tenant
    ):
        messages.append(
            Error(
                "Microsoft OAuth tenant must be common, organizations, consumers, or a tenant GUID",
                id="oauth.E003",
            )
        )
    if not 60 <= settings.OAUTH_STATE_TTL_SECONDS <= 1800:
        messages.append(
            Error("OAuth state TTL must be between 60 and 1800 seconds", id="oauth.E004")
        )
    if not 0 < settings.OAUTH_HTTP_TIMEOUT_SECONDS <= 60:
        messages.append(
            Error("OAuth HTTP timeout must be greater than 0 and at most 60", id="oauth.E005")
        )
    if not 0 <= settings.OAUTH_CLOCK_SKEW_SECONDS <= 300:
        messages.append(
            Error("OAuth clock skew must be between 0 and 300 seconds", id="oauth.E006")
        )
    if settings.OAUTH_STORE_PROVIDER_TOKENS and not settings.OAUTH_ENCRYPTION_KEY:
        messages.append(
            Warning(
                "Stored provider tokens use a key derived from DJANGO_SECRET_KEY",
                hint="Set DJANGO_OAUTH_ENCRYPTION_KEY to permit independent key rotation.",
                id="oauth.W002",
            )
        )
    return messages
