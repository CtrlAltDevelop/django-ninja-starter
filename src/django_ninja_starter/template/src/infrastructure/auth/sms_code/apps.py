from django.apps import AppConfig


class AuthSmsCodeConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "infrastructure.auth.sms_code"
    label = "auth_sms_code"
    verbose_name = "Auth - SMS Code"
