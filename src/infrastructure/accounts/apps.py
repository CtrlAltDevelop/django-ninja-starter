from django.apps import AppConfig

from infrastructure.common.appsettings import AppSettings, Requirement


class AccountsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "infrastructure.accounts"
    label = "accounts"
    verbose_name = "Accounts"

    settings_spec = AppSettings(
        title="Accounts",
        summary="The custom user model every login resolves to, and its profile.",
        requirements=(
            Requirement(
                "ACCOUNTS_DEFAULT_LOCALE",
                env="DJANGO_ACCOUNTS_DEFAULT_LOCALE",
                purpose="the locale a new profile starts with",
                required=True,
            ),
            Requirement(
                "ACCOUNTS_DEFAULT_TIMEZONE",
                env="DJANGO_ACCOUNTS_DEFAULT_TIMEZONE",
                purpose="the time zone a new profile starts with",
                required=True,
            ),
            Requirement(
                "AUTH_USER_MODEL",
                env="DJANGO_AUTH_USER_MODEL",
                purpose="the model every login resolves to and every table points at",
                required=True,
            ),
        ),
    )

    def ready(self) -> None:
        from infrastructure.accounts import signals  # noqa: F401
