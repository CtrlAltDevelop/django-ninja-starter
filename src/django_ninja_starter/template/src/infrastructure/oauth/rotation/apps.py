from django.apps import AppConfig


class OAuthRotationConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "infrastructure.oauth.rotation"
    label = "oauth_rotation"
    verbose_name = "OAuth Refresh Rotation"
