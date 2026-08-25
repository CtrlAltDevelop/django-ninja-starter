from django.apps import AppConfig

from infrastructure.common.appsettings import AppSettings, Requirement


class OAuthCoreConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "infrastructure.oauth.core"
    label = "oauth_core"
    verbose_name = "OAuth Core"

    settings_spec = AppSettings(
        title="OAuth core",
        summary="Clients, scopes, consents, social accounts, and the signing layer.",
        requirements=(
            Requirement(
                "OAUTH_STATE_TTL_SECONDS",
                env="DJANGO_OAUTH_STATE_TTL_SECONDS",
                purpose="how long a started social login may take to come back",
                minimum=60,
                maximum=1800,
            ),
            Requirement(
                "OAUTH_HTTP_TIMEOUT_SECONDS",
                env="DJANGO_OAUTH_HTTP_TIMEOUT_SECONDS",
                purpose="how long to wait on a provider's token or profile endpoint",
                minimum=0.1,
                maximum=60,
            ),
            Requirement(
                "OAUTH_CLOCK_SKEW_SECONDS",
                env="DJANGO_OAUTH_CLOCK_SKEW_SECONDS",
                purpose="the tolerance allowed when validating a provider's ID token",
                minimum=0,
                maximum=300,
            ),
        ),
    )

    def ready(self) -> None:
        from infrastructure.oauth.core import checks  # noqa: F401
