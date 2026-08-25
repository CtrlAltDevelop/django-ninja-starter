from django.apps import AppConfig

from infrastructure.common.appsettings import AppSettings, Requirement


class OAuthSlidingConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "infrastructure.oauth.sliding"
    label = "oauth_sliding"
    verbose_name = "OAuth Sliding Tokens"

    settings_spec = AppSettings(
        title="Sliding tokens",
        summary="One token that extends itself while it is being used, inside an absolute bound.",
        requirements=(
            Requirement(
                "AUTH_SLIDING_IDLE_TIMEOUT_SECONDS",
                env="DJANGO_AUTH_SLIDING_IDLE_TIMEOUT_SECONDS",
                purpose="how long a token survives without being used",
                minimum=60,
                maximum=86400,
            ),
            Requirement(
                "AUTH_REFRESH_TOKEN_TTL_SECONDS",
                env="DJANGO_AUTH_REFRESH_TOKEN_TTL_SECONDS",
                purpose="the absolute lifetime no amount of sliding can exceed",
                minimum=300,
                maximum=31536000,
            ),
        ),
    )
