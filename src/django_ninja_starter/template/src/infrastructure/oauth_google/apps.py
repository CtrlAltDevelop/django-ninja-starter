from django.apps import AppConfig


class OAuthGoogleConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "infrastructure.oauth_google"
    verbose_name = "OAuth - Google"
