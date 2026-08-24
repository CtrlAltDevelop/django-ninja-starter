from django.apps import AppConfig


class OAuthSlidingConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "infrastructure.oauth_sliding"
    verbose_name = "OAuth Sliding Tokens"
