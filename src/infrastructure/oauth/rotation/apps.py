from django.apps import AppConfig

from infrastructure.common.appsettings import AppSettings, Requirement


class OAuthRotationConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "infrastructure.oauth.rotation"
    label = "oauth_rotation"
    verbose_name = "OAuth Refresh Rotation"

    settings_spec = AppSettings(
        title="Rotating refresh tokens",
        summary="Single-use refresh tokens with rotation ancestry and reuse detection.",
        requirements=(
            Requirement(
                "AUTH_ACCESS_TOKEN_TTL_SECONDS",
                env="DJANGO_AUTH_ACCESS_TOKEN_TTL_SECONDS",
                purpose="how long each access token lasts before a rotation is needed",
                minimum=60,
                maximum=86400,
            ),
            Requirement(
                "AUTH_REFRESH_TOKEN_TTL_SECONDS",
                env="DJANGO_AUTH_REFRESH_TOKEN_TTL_SECONDS",
                purpose="how long a token family lives before a full sign-in is required",
                minimum=300,
                maximum=31536000,
            ),
        ),
    )
