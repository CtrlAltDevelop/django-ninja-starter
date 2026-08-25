"""Records every first-party login method shares."""

import uuid
from typing import Final

from django.conf import settings
from django.db import models
from django.utils import timezone


class PhoneNumber(models.Model):
    """A number an account has proven it controls.

    Kept in ``auth_core`` rather than in the SMS login app because the second
    factor needs the same verified number, and a project may enable either one
    without the other.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="auth_phone_numbers",
    )
    number = models.CharField(max_length=20, unique=True)
    is_verified = models.BooleanField(default=False)
    is_primary = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    verified_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["user"],
                condition=models.Q(is_primary=True),
                name="auth_unique_primary_phone",
            )
        ]
        indexes = [models.Index(fields=["user", "is_verified"])]

    def __str__(self) -> str:
        return self.number

    def mark_verified(self) -> None:
        self.is_verified = True
        self.verified_at = timezone.now()
        self.save(update_fields=["is_verified", "verified_at"])


class AuthEventType:
    """The event names, typed as plain strings.

    ``TextChoices`` members carry their label as a second tuple element, which a
    type checker reads as ``tuple[str, str]`` rather than ``str``. Keeping the
    values here and building the choices from them gives the rest of the code
    something it can pass around as the string it actually is, with no second
    place for the names to drift to.
    """

    SIGNUP: Final = "signup"
    CODE_SENT: Final = "code_sent"
    LOGIN_SUCCEEDED: Final = "login_succeeded"
    LOGIN_FAILED: Final = "login_failed"
    LOGOUT: Final = "logout"
    SECOND_FACTOR_REQUIRED: Final = "second_factor_required"
    SECOND_FACTOR_SUCCEEDED: Final = "second_factor_succeeded"
    SECOND_FACTOR_FAILED: Final = "second_factor_failed"
    SECOND_FACTOR_ENROLLED: Final = "second_factor_enrolled"
    SECOND_FACTOR_REMOVED: Final = "second_factor_removed"
    PASSWORD_RESET_REQUESTED: Final = "password_reset_requested"
    PASSWORD_CHANGED: Final = "password_changed"
    THROTTLED: Final = "throttled"


class AuthEvent(models.Model):
    """Audit trail for sign-in activity.

    Identifiers are stored as digests: the log has to answer "how many failures
    hit this address" without becoming a second copy of the user table.
    """

    class EventType(models.TextChoices):
        SIGNUP = AuthEventType.SIGNUP, "Signup"
        CODE_SENT = AuthEventType.CODE_SENT, "Code sent"
        LOGIN_SUCCEEDED = AuthEventType.LOGIN_SUCCEEDED, "Login succeeded"
        LOGIN_FAILED = AuthEventType.LOGIN_FAILED, "Login failed"
        LOGOUT = AuthEventType.LOGOUT, "Logout"
        SECOND_FACTOR_REQUIRED = AuthEventType.SECOND_FACTOR_REQUIRED, "Second factor required"
        SECOND_FACTOR_SUCCEEDED = AuthEventType.SECOND_FACTOR_SUCCEEDED, "Second factor succeeded"
        SECOND_FACTOR_FAILED = AuthEventType.SECOND_FACTOR_FAILED, "Second factor failed"
        SECOND_FACTOR_ENROLLED = AuthEventType.SECOND_FACTOR_ENROLLED, "Second factor enrolled"
        SECOND_FACTOR_REMOVED = AuthEventType.SECOND_FACTOR_REMOVED, "Second factor removed"
        PASSWORD_RESET_REQUESTED = (
            AuthEventType.PASSWORD_RESET_REQUESTED,
            "Password reset requested",
        )
        PASSWORD_CHANGED = AuthEventType.PASSWORD_CHANGED, "Password changed"
        THROTTLED = AuthEventType.THROTTLED, "Throttled"

    id = models.BigAutoField(primary_key=True)
    event_type = models.CharField(max_length=32, choices=EventType)
    method = models.CharField(max_length=32, blank=True)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="auth_events",
    )
    identifier_hash = models.CharField(max_length=64, blank=True)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    user_agent = models.TextField(blank=True)
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["event_type", "created_at"]),
            models.Index(fields=["user", "created_at"]),
            models.Index(fields=["identifier_hash", "created_at"]),
        ]

    def __str__(self) -> str:
        return f"{self.event_type}:{self.id}"
