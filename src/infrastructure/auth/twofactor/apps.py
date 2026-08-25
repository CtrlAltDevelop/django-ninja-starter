from django.apps import AppConfig


class AuthTwoFactorConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "infrastructure.auth.twofactor"
    label = "auth_twofactor"
    verbose_name = "Auth - Two Factor"
