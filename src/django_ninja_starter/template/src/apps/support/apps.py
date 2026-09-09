from django.apps import AppConfig

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
