from django.apps import AppConfig

from infrastructure.common.appsettings import AppSettings, Requirement


class AuthSmsCodeConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "infrastructure.auth.sms_code"
    label = "auth_sms_code"
    verbose_name = "Auth - SMS Code"

    settings_spec = AppSettings(
        title="SMS-code login",
        summary="Sign-up and sign-in with a one-time code sent to a phone number.",
        requirements=(
            Requirement(
                "AUTH_SMS_BACKEND",
                env="DJANGO_AUTH_SMS_BACKEND",
                purpose="the carrier that delivers the text",
                required=True,
                unsafe_defaults=("infrastructure.auth.core.delivery.ConsoleSmsBackend",),
                hint=(
                    "The console backend writes the code to the log, so anyone who can "
                    "read logs can sign in as anyone."
                ),
            ),
            Requirement(
                "AUTH_SMS_FROM",
                env="DJANGO_AUTH_SMS_FROM",
                purpose="the sender ID or number the message comes from",
                recommended=True,
                hint="Most carriers require a registered originator.",
            ),
        ),
    )
