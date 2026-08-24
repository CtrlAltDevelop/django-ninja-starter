from django.apps import AppConfig


class OAuthCoreConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "infrastructure.oauth_core"
    verbose_name = "OAuth Core"

    def ready(self) -> None:
        from infrastructure.oauth_core import checks  # noqa: F401
