from django.apps import AppConfig

from infrastructure.common.appsettings import AppSettings, Requirement


class AuthCoreConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "infrastructure.auth.core"
    label = "auth_core"
    verbose_name = "Auth Core"

    settings_spec = AppSettings(
        title="Authentication core",
        summary="Shared identity records, the challenge store, and the audit trail.",
        requirements=(
            Requirement(
                "AUTH_CHALLENGE_STORE",
                env="DJANGO_AUTH_CHALLENGE_STORE",
                purpose="where pending two-step logins are held",
                required=True,
                unsafe_defaults=("infrastructure.auth.core.challenges.LocMemChallengeStore",),
                hint=(
                    "The in-memory store does not survive a restart or reach a second "
                    "worker, so step two lands on a process that never saw step one."
                ),
            ),
            Requirement(
                "AUTH_REDIS_URL",
                env="DJANGO_AUTH_REDIS_URL",
                purpose="the Redis instance holding challenges and rate-limit counters",
                required=True,
            ),
            Requirement(
                "AUTH_CHALLENGE_TTL_SECONDS",
                env="DJANGO_AUTH_CHALLENGE_TTL_SECONDS",
                purpose="how long a delivered code stays redeemable",
                minimum=60,
                maximum=3600,
            ),
            Requirement(
                "AUTH_CHALLENGE_MAX_ATTEMPTS",
                env="DJANGO_AUTH_CHALLENGE_MAX_ATTEMPTS",
                purpose="how many wrong codes a challenge tolerates before it is destroyed",
                minimum=1,
                maximum=20,
            ),
            Requirement(
                "AUTH_CODE_DIGITS",
                env="DJANGO_AUTH_CODE_DIGITS",
                purpose="the length of a one-time code",
                minimum=4,
                maximum=10,
            ),
            Requirement(
                "AUTH_PENDING_LOGIN_TTL_SECONDS",
                env="DJANGO_AUTH_PENDING_LOGIN_TTL_SECONDS",
                purpose="how long a login may wait between its first and second factor",
                minimum=60,
                maximum=3600,
            ),
            Requirement(
                "AUTH_RESEND_COOLDOWN_SECONDS",
                env="DJANGO_AUTH_RESEND_COOLDOWN_SECONDS",
                purpose="the wait before the same destination may be sent another code",
                minimum=0,
                maximum=3600,
            ),
            Requirement(
                "AUTH_MAX_SENDS_PER_HOUR",
                env="DJANGO_AUTH_MAX_SENDS_PER_HOUR",
                purpose="the hourly ceiling on codes to one destination",
                minimum=0,
                maximum=1000,
            ),
        ),
    )

    def ready(self) -> None:
        from infrastructure.auth.core import checks  # noqa: F401
