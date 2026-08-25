from django.apps import AppConfig

from infrastructure.common.appsettings import AppSettings, Requirement


class AuthMagicLinkConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "infrastructure.auth.magic_link"
    label = "auth_magic_link"
    verbose_name = "Auth - Magic Link"

    settings_spec = AppSettings(
        title="Magic-link login",
        summary="Sign-up and sign-in by following a single-use emailed link.",
        requirements=(
            Requirement(
                "AUTH_MAGIC_LINK_BASE_URL",
                env="DJANGO_AUTH_MAGIC_LINK_BASE_URL",
                purpose="the page that reads the token out of the URL and posts it back",
                required=True,
                hint="Without it the emailed link points nowhere and no login can complete.",
            ),
            Requirement(
                "AUTH_EMAIL_BACKEND",
                env="DJANGO_AUTH_EMAIL_BACKEND",
                purpose="how the link reaches the address",
                required=True,
                unsafe_defaults=("infrastructure.auth.core.delivery.ConsoleEmailBackend",),
            ),
            Requirement(
                "AUTH_EMAIL_FROM",
                env="DJANGO_AUTH_EMAIL_FROM",
                purpose="the sender address recipients will see",
                required=True,
                unsafe_defaults=("no-reply@example.com",),
            ),
        ),
    )
