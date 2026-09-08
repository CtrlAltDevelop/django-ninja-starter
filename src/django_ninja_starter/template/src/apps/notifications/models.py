"""What a notification is, who it is for, and what each account has done with it.

This app is meant to be lifted out and dropped into another Django project, so
it owns everything it needs and asks the project for almost nothing: settings it
defaults for itself, a router mount, and one line of WebSocket routing.

Two rows, because there are two questions and they have different cardinality.

A **notification** is the thing that happened. It is addressed either to one
account or to everybody, and which of the two it is has to be a stored fact
rather than "recipient happens to be null": a broadcast and a message whose
recipient was deleted are not the same thing, and only one of them should still
be delivered. A database constraint keeps the two columns agreeing.

A **receipt** is one account's state for one notification: when they read it,
and when they dismissed it. That state cannot live on the notification itself,
because a global notification is read and dismissed by each person separately --
there is no single ``read_at`` to write, and no row anybody may delete. Making
the per-account row the only representation means the unread query is the same
shape for both audiences, instead of a broadcast being the special case that
every caller forgets.

Both are therefore annotations rather than columns: see
:meth:`NotificationQuerySet.with_state`, which leaves ``read_at`` null for
anything this account has not read and ``dismissed_at`` null for anything it has
not cleared away.

A receipt exists as soon as an account does *anything* to a notification, so its
existence is no longer the read state -- ``read_at`` is, and it is nullable so
that reading can be undone and so that dismissing does not imply reading.
"""

import uuid
from collections.abc import Iterable
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

    def with_state(self, user: Any) -> "NotificationQuerySet":
        """Annotate ``read_at`` and ``dismissed_at`` for this account.

        Correlated subqueries rather than a join, because the same notification
        has one receipt per reader and a join would multiply the broadcast rows
        by everybody who has seen them. Two subqueries against the same row
        rather than one, because a queryset cannot carry two columns out of a
        single ``Subquery``.
        """
        receipts = NotificationReceipt.objects.filter(notification=OuterRef("pk"), user=user)
        return self.annotate(
            read_at=Subquery(receipts.values("read_at")[:1]),
            dismissed_at=Subquery(receipts.values("dismissed_at")[:1]),
        )

    def with_read_state(self, user: Any) -> "NotificationQuerySet":
        """Deprecated alias for :meth:`with_state`, kept for callers outside this app."""
        return self.with_state(user)

    def unread(self) -> "NotificationQuerySet":
        """Only the unread ones. Requires :meth:`with_state` to have run first."""
        return self.filter(read_at__isnull=True)

    def read(self) -> "NotificationQuerySet":
        """Only the read ones. Requires :meth:`with_state` to have run first."""
        return self.filter(read_at__isnull=False)

    def undismissed(self) -> "NotificationQuerySet":
        """Only what is still in the tray. Requires :meth:`with_state` to have run first."""
        return self.filter(dismissed_at__isnull=True)

    def for_user(self, user: Any, *, include_dismissed: bool = False) -> "NotificationQuerySet":
        """The list one account is entitled to see, each row carrying its own state.

        Dismissed rows are left out by default, because dismissing is a request
        not to be shown something again and every caller that forgot to filter
        would be breaking that promise.
        """
        notifications = self.visible_to(user).with_state(user)
        return notifications if include_dismissed else notifications.undismissed()


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
            # Pruning walks the table by age alone, across both audiences.
            models.Index(fields=["created_at"]),
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
    """One account's state for one notification: when it was read, when dismissed.

    Both timestamps are nullable, and a receipt with neither set is possible --
    briefly, when reading is undone. That is cheaper than deleting the row and
    re-creating it the next time, and it keeps "has this account ever touched
    this?" answerable.
    """

    notification = models.ForeignKey(
        Notification, on_delete=models.CASCADE, related_name="receipts"
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="notification_receipts"
    )
    read_at = models.DateTimeField(
        null=True, blank=True, help_text="When this account read it. Empty means unread."
    )
    dismissed_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="When this account cleared it away. Empty means still in the tray.",
    )

    class Meta:
        ordering = ["-read_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["notification", "user"], name="notification_read_once_per_account"
            )
        ]

    def __str__(self) -> str:
        return f"{self.user} / {self.notification_id}"

    @property
    def is_read(self) -> bool:
        return self.read_at is not None

    @property
    def is_dismissed(self) -> bool:
        return self.dismissed_at is not None


def _touch(user: Any, notification: "Notification", **state: Any) -> bool:
    """Set state on this account's receipt, creating it if this is the first touch.

    Returns whether the state actually changed, which is what every caller wants
    to report back: "3 marked read" should not count the two that already were.
    """
    receipt, created = NotificationReceipt.objects.get_or_create(
        notification=notification, user=user, defaults=state
    )
    if created:
        return any(value is not None for value in state.values())
    changed = {
        field: value
        for field, value in state.items()
        if (getattr(receipt, field) is None) != (value is None)
    }
    if not changed:
        return False
    for field, value in changed.items():
        setattr(receipt, field, value)
    receipt.save(update_fields=list(changed))
    return True


def mark_read(user: Any, notification: Notification) -> bool:
    """Record that this account has read this notification. Returns whether it changed.

    Idempotent on purpose: a client that fires the same "I read this" twice --
    over the socket and again over HTTP, say -- should not get an error for it.
    """
    return _touch(user, notification, read_at=timezone.now())


def mark_unread(user: Any, notification: Notification) -> bool:
    """Undo a read. Returns whether it changed -- unread already is not an error."""
    return _touch(user, notification, read_at=None)


def dismiss(user: Any, notification: Notification) -> bool:
    """Take one notification out of this account's tray. Returns whether it changed.

    A dismissal is per account and never a delete, because a broadcast belongs to
    everybody: one person clearing an announcement must not remove it from anyone
    else's tray. Dismissing also marks it read, since a client that never showed
    it again while still counting it in the badge would be lying about the badge.
    """
    return _touch(user, notification, read_at=timezone.now(), dismissed_at=timezone.now())


def restore(user: Any, notification: Notification) -> bool:
    """Undo a dismissal, putting it back in the tray. Read state is left alone."""
    return _touch(user, notification, dismissed_at=None)


def _apply_to_all(user: Any, queryset: Any, **state: Any) -> int:
    """Apply one state change across a whole queryset in two round trips.

    Written as a bulk update of the receipts that exist plus a bulk insert of the
    ones that do not, rather than a loop, so catching up on a long-neglected
    inbox does not cost a query per row. ``ignore_conflicts`` covers the race
    where a receipt lands between the two.
    """
    identifiers = list(queryset.values_list("id", flat=True))
    if not identifiers:
        return 0
    existing = set(
        NotificationReceipt.objects.filter(notification_id__in=identifiers, user=user).values_list(
            "notification_id", flat=True
        )
    )
    if existing:
        NotificationReceipt.objects.filter(notification_id__in=existing, user=user).update(**state)
    missing = [
        NotificationReceipt(notification_id=identifier, user=user, **state)
        for identifier in identifiers
        if identifier not in existing
    ]
    if missing:
        NotificationReceipt.objects.bulk_create(missing, ignore_conflicts=True)
    return len(identifiers)


def mark_all_read(user: Any) -> int:
    """Read everything still in this account's tray. Returns how many that changed."""
    unread = Notification.objects.for_user(user).unread()
    return _apply_to_all(user, unread, read_at=timezone.now())


def dismiss_all(user: Any) -> int:
    """Empty this account's tray. Returns how many notifications it cleared."""
    outstanding = Notification.objects.for_user(user)
    now = timezone.now()
    return _apply_to_all(user, outstanding, read_at=now, dismissed_at=now)


def unread_count(user: Any) -> int:
    """How many undismissed notifications this account has not read."""
    return Notification.objects.for_user(user).unread().count()


def prune(older_than: Any) -> int:
    """Delete notifications created before ``older_than``. Returns how many went.

    Receipts go with them, by cascade. This is the whole of retention: there is
    no archive, because a notification nobody has looked at in months is not
    worth the storage of pretending it might be.
    """
    deleted, _ = Notification.objects.filter(created_at__lt=older_than).delete()
    return deleted


def receipts_for(
    user: Any, notifications: Iterable[Notification]
) -> dict[Any, "NotificationReceipt"]:
    """This account's receipts for a batch of notifications, keyed by notification id."""
    identifiers = [notification.pk for notification in notifications]
    return {
        receipt.notification_id: receipt
        for receipt in NotificationReceipt.objects.filter(
            notification_id__in=identifiers, user=user
        )
    }
