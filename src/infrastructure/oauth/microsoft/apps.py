from django.apps import AppConfig


class OAuthMicrosoftConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "infrastructure.oauth.microsoft"
    label = "oauth_microsoft"
    verbose_name = "OAuth - Microsoft"
