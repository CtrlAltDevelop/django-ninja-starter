from django.apps import AppConfig

from infrastructure.common.appsettings import AppSettings, Requirement


class NotificationsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.notifications"
    label = "notifications"
    verbose_name = "Notifications"

    settings_spec = AppSettings(
        title="Notifications",
        summary="Stored notifications, a read API, and a WebSocket that pushes new ones.",
        requirements=(
            Requirement(
                "NOTIFICATIONS_BROKER",
                env="DJANGO_NOTIFICATIONS_BROKER",
                purpose="how a notification created in one process reaches sockets held by another",
                recommended=True,
                unsafe_defaults=("apps.notifications.broadcast.MemoryBroker",),
                hint=(
                    "The in-memory broker only fans out inside a single process, so under "
                    "more than one worker most connected clients never see the message. "
                    "Use apps.notifications.broadcast.RedisBroker in production."
                ),
            ),
            Requirement(
                "NOTIFICATIONS_SOCKET_BACKLOG",
                env="DJANGO_NOTIFICATIONS_SOCKET_BACKLOG",
                purpose="how many unread notifications a client is caught up with on connect",
                minimum=0,
                maximum=500,
            ),
        ),
    )

    def ready(self) -> None:
        """Connect the signal that broadcasts a notification the moment it is saved."""
        from apps.notifications import events

        events.connect()
