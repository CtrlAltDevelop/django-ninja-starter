from django.apps import AppConfig


class AuthEmailCodeConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "infrastructure.auth.email_code"
    label = "auth_email_code"
    verbose_name = "Auth - Email Code"
