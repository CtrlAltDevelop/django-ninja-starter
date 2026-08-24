from django.apps import AppConfig


class OAuthSessionConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "infrastructure.oauth_session"
    verbose_name = "OAuth Sessions"
