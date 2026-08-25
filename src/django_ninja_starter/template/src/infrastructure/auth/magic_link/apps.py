from django.apps import AppConfig


class AuthMagicLinkConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "infrastructure.auth.magic_link"
    label = "auth_magic_link"
    verbose_name = "Auth - Magic Link"
