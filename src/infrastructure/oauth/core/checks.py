"""OAuth checks that no per-setting declaration can express.

Which credentials each provider needs is stated on the provider app configs and
validated by :mod:`infrastructure.common.checks`. What is left here are the
format rules and the one question that spans two settings.
"""

import re
from urllib.parse import urlsplit

from django.conf import settings
from django.core.checks import CheckMessage, Error, Warning, register

MICROSOFT_TENANT_PATTERN = re.compile(
    r"^(common|organizations|consumers|[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-"
    r"[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12})$"
)


@register()
def check_social_oauth_settings(**kwargs: object) -> list[CheckMessage]:
    messages: list[CheckMessage] = []
    if "apple" in settings.OAUTH_PROVIDERS:
        redirect_uri = str(settings.OAUTH_PROVIDER_CONFIG["apple"].get("redirect_uri", ""))
        if redirect_uri and urlsplit(redirect_uri).scheme != "https":
            messages.append(
                Error(
                    "Apple OAuth redirect URI must use HTTPS",
                    hint="Apple posts the callback cross-site and refuses plain HTTP.",
                    id="oauth.E002",
                )
            )
    if "microsoft" in settings.OAUTH_PROVIDERS:
        tenant = str(settings.OAUTH_PROVIDER_CONFIG["microsoft"].get("tenant", ""))
        if not MICROSOFT_TENANT_PATTERN.fullmatch(tenant):
            messages.append(
                Error(
                    "Microsoft OAuth tenant must be common, organizations, "
                    "consumers, or a tenant GUID",
                    id="oauth.E003",
                )
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
