"""What a notification is, who it is for, and who has already read it.

This app is meant to be lifted out and dropped into another Django project, so
it owns everything it needs and asks the project for almost nothing: settings it
defaults for itself, a router mount, and one line of WebSocket routing.

Two rows, because there are two questions and they have different cardinality.

A **notification** is the thing that happened. It is addressed either to one
account or to everybody, and which of the two it is has to be a stored fact
rather than "recipient happens to be null": a broadcast and a message whose
recipient was deleted are not the same thing, and only one of them should still
be delivered. A database constraint keeps the two columns agreeing.

A **receipt** is one account having read one notification. Read state cannot
live on the notification itself, because a global notification is read by each
person separately -- there is no single ``read_at`` to write. Making the
per-account row the only representation means the unread query is the same
shape for both audiences, instead of a broadcast being the special case that
every caller forgets.

Read state is therefore an annotation rather than a column: see
:meth:`NotificationQuerySet.with_read_state`, which leaves ``read_at`` null for
anything this account has not read.
"""

import uuid
from typing import Any

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import OuterRef, Q, Subquery
from django.utils import timezone


class Audience(models.TextChoices):
    """Who a notification is addressed to."""

    GLOBAL = "global", "Everyone"
    USER = "user", "One account"


class Level(models.TextChoices):
    """How loudly a client should render it. Advisory; nothing here branches on it."""

    INFO = "info", "Info"
    SUCCESS = "success", "Success"
    WARNING = "warning", "Warning"
    ERROR = "error", "Error"


class NotificationQuerySet(models.QuerySet["Notification"]):
    def visible_to(self, user: Any) -> "NotificationQuerySet":
        """Everything addressed to this account, plus everything addressed to everybody."""
        return self.filter(Q(audience=Audience.GLOBAL) | Q(recipient=user))

    def with_read_state(self, user: Any) -> "NotificationQuerySet":
        """Annotate ``read_at``: when this account read the row, or null if it has not.

        A correlated subquery rather than a join, because the same notification
        has one receipt per reader and a join would multiply the broadcast rows
        by everybody who has seen them.
        """
        receipts = NotificationReceipt.objects.filter(notification=OuterRef("pk"), user=user)
        return self.annotate(read_at=Subquery(receipts.values("read_at")[:1]))

    def unread(self) -> "NotificationQuerySet":
        """Only the unread ones. Requires :meth:`with_read_state` to have run first."""
        return self.filter(read_at__isnull=True)

    def for_user(self, user: Any) -> "NotificationQuerySet":
        """The list one account is entitled to see, each row carrying its read state."""
        return self.visible_to(user).with_read_state(user)


class Notification(models.Model):
    """One thing worth telling somebody about."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    audience = models.CharField(
        max_length=16,
        choices=Audience.choices,
        default=Audience.USER,
        help_text="Whether this reaches one account or everybody.",
    )
    recipient = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="notifications",
        help_text="The account this is for. Empty -- and required to be -- for a broadcast.",
    )
    subject = models.CharField(max_length=200)
    body = models.TextField(blank=True)
    level = models.CharField(max_length=16, choices=Level.choices, default=Level.INFO)
    link = models.CharField(
        max_length=500,
        blank=True,
        help_text="Where a client should go when somebody opens this.",
    )
    data = models.JSONField(
        default=dict,
        blank=True,
        help_text="Anything the sending code wants the client to have. Sent verbatim.",
    )
    created_at = models.DateTimeField(default=timezone.now, editable=False)

    objects = NotificationQuerySet.as_manager()

    class Meta:
        ordering = ["-created_at", "-id"]
        indexes = [
            models.Index(fields=["recipient", "-created_at"]),
            models.Index(fields=["audience", "-created_at"]),
        ]
        constraints = [
            models.CheckConstraint(
                condition=(
                    Q(audience=Audience.USER, recipient__isnull=False)
                    | Q(audience=Audience.GLOBAL, recipient__isnull=True)
                ),
                name="notification_audience_matches_recipient",
                violation_error_message=(
                    "A user notification needs a recipient; a global one must not have any."
                ),
            )
        ]

    def __str__(self) -> str:
        return self.subject

    @property
    def is_global(self) -> bool:
        return self.audience == Audience.GLOBAL

    def clean(self) -> None:
        """Say what the database constraint says, early enough to be a form error.

        The constraint is the guarantee; this is the sentence somebody filling in
        the admin gets instead of an IntegrityError traceback.
        """
        if self.audience == Audience.GLOBAL and self.recipient_id is not None:
            raise ValidationError(
                {"recipient": "A global notification reaches everybody. Leave this empty."}
            )
        if self.audience == Audience.USER and self.recipient_id is None:
            raise ValidationError({"recipient": "Choose who this notification is for."})


class NotificationReceipt(models.Model):
    """One account having read one notification. Its existence is the read state."""

    notification = models.ForeignKey(
        Notification, on_delete=models.CASCADE, related_name="receipts"
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="notification_receipts"
    )
    read_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ["-read_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["notification", "user"], name="notification_read_once_per_account"
            )
        ]

    def __str__(self) -> str:
        return f"{self.user} read {self.notification_id}"


def mark_read(user: Any, notification: Notification) -> bool:
    """Record that this account has read this notification. Returns whether it was new.

    Idempotent on purpose: a client that fires the same "I read this" twice --
    over the socket and again over HTTP, say -- should not get an error for it.
    """
    _, created = NotificationReceipt.objects.get_or_create(notification=notification, user=user)
    return created


def mark_all_read(user: Any) -> int:
    """Read everything this account can see. Returns how many rows that changed.

    Written as one bulk insert of the missing receipts rather than a loop, so
    catching up on a long-neglected inbox is one round trip. ``ignore_conflicts``
    covers the race where a receipt lands between the query and the insert.
    """
    unread = Notification.objects.for_user(user).unread()
    receipts = [
        NotificationReceipt(notification_id=notification_id, user=user)
        for notification_id in unread.values_list("id", flat=True)
    ]
    if not receipts:
        return 0
    NotificationReceipt.objects.bulk_create(receipts, ignore_conflicts=True)
    return len(receipts)


def unread_count(user: Any) -> int:
    return Notification.objects.for_user(user).unread().count()
