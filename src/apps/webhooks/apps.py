from django.apps import AppConfig

from infrastructure.common.appsettings import AppSettings, Requirement


class WebhooksConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.webhooks"
    label = "webhooks"
    verbose_name = "Webhooks"

    settings_spec = AppSettings(
        title="Webhooks",
        summary="Signed outgoing webhooks, retried with backoff by `manage.py webhooks_deliver`.",
        requirements=(
            Requirement(
                "WEBHOOKS_ENABLED",
                env="DJANGO_WEBHOOKS_ENABLED",
                purpose="whether this deployment carries outgoing webhooks at all",
                required=True,
            ),
            Requirement(
                "WEBHOOKS_MAX_ATTEMPTS",
                env="DJANGO_WEBHOOKS_MAX_ATTEMPTS",
                purpose="how many times a delivery is tried before it is marked failed",
                minimum=1,
            ),
            Requirement(
                "WEBHOOKS_BACKOFF_SECONDS",
                env="DJANGO_WEBHOOKS_BACKOFF_SECONDS",
                purpose="the wait after the first failure; it doubles after each one",
                minimum=1,
            ),
            Requirement(
                "WEBHOOKS_TIMEOUT_SECONDS",
                env="DJANGO_WEBHOOKS_TIMEOUT_SECONDS",
                purpose="how long one delivery waits for the receiver to answer",
                minimum=1,
            ),
        ),
    )
