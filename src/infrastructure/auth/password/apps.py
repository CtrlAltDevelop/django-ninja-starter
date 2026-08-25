from django.apps import AppConfig


class AuthPasswordConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "infrastructure.auth.password"
    label = "auth_password"
    verbose_name = "Auth - Password"
