from django.apps import AppConfig

from infrastructure.common.appsettings import AppSettings, Requirement


class AuthTwoFactorConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "infrastructure.auth.twofactor"
    label = "auth_twofactor"
    verbose_name = "Auth - Two Factor"

    settings_spec = AppSettings(
        title="Two-factor authentication",
        summary="Authenticator apps, delivered codes, and printable recovery codes.",
        requirements=(
            Requirement(
                "AUTH_TOTP_ISSUER",
                env="DJANGO_AUTH_TOTP_ISSUER",
                purpose="the name an authenticator app shows beside the account",
                required=True,
                hint="Users pick the right code by this label, so make it recognisable.",
            ),
            Requirement(
                "AUTH_RECOVERY_CODE_COUNT",
                env="DJANGO_AUTH_RECOVERY_CODE_COUNT",
                purpose="how many recovery codes a set contains",
                minimum=5,
                maximum=30,
            ),
        ),
    )
