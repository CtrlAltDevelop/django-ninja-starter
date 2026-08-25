from django.apps import AppConfig


class OAuthAppleConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "infrastructure.oauth.apple"
    label = "oauth_apple"
    verbose_name = "OAuth - Apple"
