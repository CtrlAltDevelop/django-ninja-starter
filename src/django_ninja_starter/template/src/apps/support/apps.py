from django.apps import AppConfig
from django.conf import settings

from infrastructure.common.appsettings import AppSettings, Requirement


class SupportConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.support"
    label = "support"
    verbose_name = "Support"

    settings_spec = AppSettings(
        title="Support",
        summary=(
            "Tickets and live chat between a client and the desk, over REST, GraphQL, "
            "gRPC and a WebSocket."
        ),
        requirements=(
            Requirement(
                "SUPPORT_ENABLED",
                env="DJANGO_SUPPORT_ENABLED",
                purpose=(
                    "whether this deployment carries the support desk at all -- its "
                    "tables, its routes, its admin and its socket"
                ),
                required=True,
                hint=(
                    "The app is installed, so something put it in INSTALLED_APPS while "
                    "DJANGO_SUPPORT_ENABLED was off. Set it to true, or drop the app: "
                    "half enabled, it migrates its tables and publishes none of its "
                    "routes."
                ),
            ),
            Requirement(
                "SUPPORT_WS_PATH",
                env="DJANGO_SUPPORT_WS_PATH",
                purpose=(
                    "where the live conversation is mounted, which a reverse proxy has "
                    "to be told about"
                ),
                required=True,
                pattern=r"/\S*",
                pattern_description="be a path beginning with a slash, such as /ws/support",
            ),
            Requirement(
                "SUPPORT_REFERENCE_PREFIX",
                env="DJANGO_SUPPORT_REFERENCE_PREFIX",
                purpose="the letters in front of a ticket reference, as in SUP-3F7A2B",
                required=True,
                pattern=r"[A-Za-z0-9]{1,8}",
                pattern_description="be one to eight letters or digits, with no separator",
            ),
            Requirement(
                "SUPPORT_CHANNEL_PREFIX",
                env="DJANGO_SUPPORT_CHANNEL_PREFIX",
                purpose=(
                    "what this deployment's broadcast channels are named, so two "
                    "deployments sharing a Redis do not deliver each other's messages"
                ),
                required=True,
            ),
            Requirement(
                "SUPPORT_REDIS_URL",
                env="DJANGO_SUPPORT_REDIS_URL",
                purpose="the Redis the broker fans out through",
                required=True,
                applies_when=lambda: "Redis" in settings.SUPPORT_BROKER,
                hint=(
                    "The broker is the Redis one, and it has nothing to connect to. Set "
                    "this, or DJANGO_AUTH_REDIS_URL, which it falls back to."
                ),
            ),
            Requirement(
                "SUPPORT_UPLOAD_PATH",
                env="DJANGO_SUPPORT_UPLOAD_PATH",
                purpose=(
                    'where a file attached to a message is written inside STORAGES["default"]'
                ),
                required=True,
                hint=(
                    "A prefix, not a filesystem path. Empty would write strangers' "
                    "attachments to the root of the store, beside everything else."
                ),
            ),
            Requirement(
                "SUPPORT_UPLOAD_EXTENSIONS",
                env="DJANGO_SUPPORT_UPLOAD_EXTENSIONS",
                purpose="what the desk accepts, as an allowlist of extensions",
                recommended=True,
                hint=(
                    "A support desk is a place strangers send you files, which is the "
                    "worst place to accept any of them. Left empty the app applies no "
                    "extension check at all."
                ),
            ),
            Requirement(
                "SUPPORT_BROKER",
                env="DJANGO_SUPPORT_BROKER",
                purpose="how a message posted in one process reaches sockets held by another",
                recommended=True,
                unsafe_defaults=("apps.support.broadcast.MemoryBroker",),
                hint=(
                    "The in-memory broker only fans out inside a single process, so under "
                    "more than one worker a client and the agent answering them are very "
                    "likely to be talking to different workers and hear nothing. Use "
                    "apps.support.broadcast.RedisBroker in production."
                ),
            ),
            Requirement(
                "SUPPORT_RETENTION_DAYS",
                env="DJANGO_SUPPORT_RETENTION_DAYS",
                purpose=(
                    "how long a closed ticket is kept before `manage.py support_prune` deletes it"
                ),
                minimum=0,
                hint=(
                    "Zero keeps everything, which is safe but grows without bound. Set a "
                    "window and schedule `manage.py support_prune`; nothing deletes "
                    "anything until you run it, and an open ticket is never deleted "
                    "however old it is."
                ),
            ),
            Requirement(
                "SUPPORT_MAX_UPLOAD_MB",
                env="DJANGO_SUPPORT_MAX_UPLOAD_MB",
                purpose="the largest file a client may attach to a message",
                minimum=0,
            ),
            Requirement(
                "SUPPORT_SOCKET_BACKLOG",
                env="DJANGO_SUPPORT_SOCKET_BACKLOG",
                purpose="how many recent messages per thread a client is caught up with on connect",
                minimum=0,
                maximum=200,
            ),
        ),
    )

    def ready(self) -> None:
        """Connect the signals that push a message and a ticket change to the sockets."""
        from apps.support import events

        events.connect()
