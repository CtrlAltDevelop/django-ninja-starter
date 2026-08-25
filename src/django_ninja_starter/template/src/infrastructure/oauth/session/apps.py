from django.apps import AppConfig

from infrastructure.common.appsettings import AppSettings, Requirement


class OAuthSessionConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "infrastructure.oauth.session"
    label = "oauth_session"
    verbose_name = "OAuth Sessions"

    settings_spec = AppSettings(
        title="Server-side sessions",
        summary="A long-lived session key that mints short, independently revocable access tokens.",
        requirements=(
            Requirement(
                "AUTH_ACCESS_TOKEN_TTL_SECONDS",
                env="DJANGO_AUTH_ACCESS_TOKEN_TTL_SECONDS",
                purpose="how long each access token minted from the session lasts",
                minimum=60,
                maximum=86400,
            ),
            Requirement(
                "AUTH_REFRESH_TOKEN_TTL_SECONDS",
                env="DJANGO_AUTH_REFRESH_TOKEN_TTL_SECONDS",
                purpose="how long the session itself lasts",
                minimum=300,
                maximum=31536000,
            ),
        ),
    )
