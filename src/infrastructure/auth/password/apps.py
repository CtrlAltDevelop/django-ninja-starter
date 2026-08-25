from django.apps import AppConfig

from infrastructure.common.appsettings import AppSettings, Requirement


class AuthPasswordConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "infrastructure.auth.password"
    label = "auth_password"
    verbose_name = "Auth - Password"

    settings_spec = AppSettings(
        title="Password login",
        summary="Sign-up, sign-in, reset and change with a password.",
        requirements=(
            Requirement(
                "AUTH_PASSWORD_RESET_BASE_URL",
                env="DJANGO_AUTH_PASSWORD_RESET_BASE_URL",
                purpose="the page a reset email points at",
                recommended=True,
                hint="Without it the email carries a bare code and no link.",
            ),
            Requirement(
                "AUTH_EMAIL_BACKEND",
                env="DJANGO_AUTH_EMAIL_BACKEND",
                purpose="how a reset code is delivered",
                required=True,
            ),
            Requirement(
                "AUTH_EMAIL_FROM",
                env="DJANGO_AUTH_EMAIL_FROM",
                purpose="the sender address on a reset email",
                required=True,
                unsafe_defaults=("no-reply@example.com",),
            ),
        ),
    )
