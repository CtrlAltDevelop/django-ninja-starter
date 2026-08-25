"""Authentication checks that no per-setting declaration can express.

Most of what this layer needs is stated on the app configs themselves and
validated by :mod:`infrastructure.common.checks`. What is left are the questions
that involve more than one setting at a time: whether the chosen token mode has
its app, whether a second factor's delivery is real, and whether the signing
configuration is usable at all.
"""

from django.conf import settings
from django.core.checks import CheckMessage, Error, Warning, register

from infrastructure.auth.core.sessions import TOKEN_MODE_APPS
from infrastructure.common.app_labels import app_installed
from infrastructure.oauth.core.jwt_tokens import ASYMMETRIC_ALGORITHMS, SUPPORTED_ALGORITHMS

CONSOLE_SMS_BACKEND = "infrastructure.auth.core.delivery.ConsoleSmsBackend"


def _jwt_messages() -> list[CheckMessage]:
    """Check the signing configuration, which no request can work around."""
    messages: list[CheckMessage] = []
    algorithm = settings.AUTH_JWT_ALGORITHM
    if algorithm not in SUPPORTED_ALGORITHMS:
        return [
            Error(
                f"DJANGO_AUTH_JWT_ALGORITHM must be one of: "
                f"{', '.join(sorted(SUPPORTED_ALGORITHMS))}",
                id="auth.E007",
            )
        ]
    if algorithm in ASYMMETRIC_ALGORITHMS:
        missing = [
            name
            for name, value in (
                ("DJANGO_AUTH_JWT_SIGNING_KEY", settings.AUTH_JWT_SIGNING_KEY),
                ("DJANGO_AUTH_JWT_VERIFYING_KEY", settings.AUTH_JWT_VERIFYING_KEY),
            )
            if not value
        ]
        if missing:
            messages.append(
                Error(
                    f"{algorithm} signing needs: {', '.join(missing)}",
                    hint="Supply the PEM-encoded key pair, or use HS256.",
                    id="auth.E008",
                )
            )
    elif not settings.AUTH_JWT_SIGNING_KEY:
        messages.append(
            Warning(
                "Access tokens are signed with a key derived from DJANGO_SECRET_KEY",
                hint=(
                    "Set DJANGO_AUTH_JWT_SIGNING_KEY so tokens can be re-keyed "
                    "without invalidating everything else the secret key protects."
                ),
                id="auth.W004",
            )
        )
    if not settings.AUTH_JWT_ISSUER:
        messages.append(
            Error(
                "DJANGO_AUTH_JWT_ISSUER must name this deployment",
                hint="Tokens carry it as `iss` and it is verified on every request.",
                id="auth.E009",
            )
        )
    if not 0 <= settings.AUTH_JWT_LEEWAY_SECONDS <= 300:
        messages.append(Error("JWT clock leeway must be between 0 and 300 seconds", id="auth.E010"))
    return messages


@register()
def check_auth_settings(**kwargs: object) -> list[CheckMessage]:
    messages: list[CheckMessage] = []
    mode = settings.AUTH_TOKEN_MODE
    if mode != "none":
        app_label = TOKEN_MODE_APPS[mode]
        if not app_installed(app_label):
            messages.append(
                Error(
                    f"DJANGO_AUTH_TOKEN_MODE={mode} needs the {app_label} app",
                    hint=f"Set DJANGO_OAUTH_MODE to {mode} or all.",
                    id="auth.E001",
                )
            )
    if "sms" in settings.AUTH_SECOND_FACTORS and settings.AUTH_SMS_BACKEND == CONSOLE_SMS_BACKEND:
        messages.append(
            Warning(
                "The SMS second factor writes its codes to the log instead of sending them",
                hint="Set DJANGO_AUTH_SMS_BACKEND to a real carrier backend.",
                id="auth.W002",
            )
        )
    if mode != "none":
        messages.extend(_jwt_messages())
    return messages
