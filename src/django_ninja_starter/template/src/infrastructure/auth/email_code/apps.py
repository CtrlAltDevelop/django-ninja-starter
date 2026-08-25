from django.apps import AppConfig

from infrastructure.common.appsettings import AppSettings, Requirement


class AuthEmailCodeConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "infrastructure.auth.email_code"
    label = "auth_email_code"
    verbose_name = "Auth - Email Code"

    settings_spec = AppSettings(
        title="Email-code login",
        summary="Sign-up and sign-in with a one-time code sent to an address.",
        requirements=(
            Requirement(
                "AUTH_EMAIL_BACKEND",
                env="DJANGO_AUTH_EMAIL_BACKEND",
                purpose="how the code reaches the address",
                required=True,
                unsafe_defaults=("infrastructure.auth.core.delivery.ConsoleEmailBackend",),
                hint="The console backend writes the code to the log instead of sending it.",
            ),
            Requirement(
                "AUTH_EMAIL_FROM",
                env="DJANGO_AUTH_EMAIL_FROM",
                purpose="the sender address recipients will see",
                required=True,
                unsafe_defaults=("no-reply@example.com",),
            ),
        ),
    )
