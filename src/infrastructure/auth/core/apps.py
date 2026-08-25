from django.apps import AppConfig


class AuthCoreConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "infrastructure.auth.core"
    label = "auth_core"
    verbose_name = "Auth Core"

    def ready(self) -> None:
        from infrastructure.auth.core import checks  # noqa: F401
