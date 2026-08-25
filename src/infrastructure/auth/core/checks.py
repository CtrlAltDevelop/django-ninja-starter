"""Django system checks for the authentication settings."""

from django.conf import settings
from django.core.checks import Error, Warning, register

from infrastructure.auth.core.sessions import TOKEN_MODE_APPS
from infrastructure.common.app_labels import app_installed

CONSOLE_SMS_BACKEND = "infrastructure.auth.core.delivery.ConsoleSmsBackend"
LOCMEM_STORE = "infrastructure.auth.core.challenges.LocMemChallengeStore"


@register()
def check_auth_settings(**kwargs: object) -> list[Error | Warning]:
    messages: list[Error | Warning] = []
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
    if not 60 <= settings.AUTH_CHALLENGE_TTL_SECONDS <= 3600:
        messages.append(
            Error("Auth challenge TTL must be between 60 and 3600 seconds", id="auth.E002")
        )
    if not 4 <= settings.AUTH_CODE_DIGITS <= 10:
        messages.append(Error("Auth code length must be between 4 and 10 digits", id="auth.E003"))
    if not 1 <= settings.AUTH_CHALLENGE_MAX_ATTEMPTS <= 20:
        messages.append(Error("Auth challenge attempts must be between 1 and 20", id="auth.E004"))
    if not 5 <= settings.AUTH_RECOVERY_CODE_COUNT <= 30:
        messages.append(Error("Auth recovery code count must be between 5 and 30", id="auth.E005"))
    if "magic_link" in settings.AUTH_METHODS and not settings.AUTH_MAGIC_LINK_BASE_URL:
        messages.append(
            Error(
                "Magic-link login needs DJANGO_AUTH_MAGIC_LINK_BASE_URL",
                hint="Point it at the page that reads the token and posts it back.",
                id="auth.E006",
            )
        )
    if settings.AUTH_CHALLENGE_STORE == LOCMEM_STORE:
        messages.append(
            Warning(
                "The in-memory challenge store does not survive across processes",
                hint="Use RedisChallengeStore outside of tests and single-worker development.",
                id="auth.W001",
            )
        )
    sms_in_use = "sms_code" in settings.AUTH_METHODS or "sms" in settings.AUTH_SECOND_FACTORS
    if sms_in_use and settings.AUTH_SMS_BACKEND == CONSOLE_SMS_BACKEND:
        messages.append(
            Warning(
                "SMS codes are being written to the log instead of sent",
                hint="Set DJANGO_AUTH_SMS_BACKEND to a real carrier backend.",
                id="auth.W002",
            )
        )
    if "password" in settings.AUTH_METHODS and not settings.AUTH_PASSWORD_RESET_BASE_URL:
        messages.append(
            Warning(
                "Password reset has no landing page configured",
                hint="Set DJANGO_AUTH_PASSWORD_RESET_BASE_URL to include a link in the email.",
                id="auth.W003",
            )
        )
    return messages
