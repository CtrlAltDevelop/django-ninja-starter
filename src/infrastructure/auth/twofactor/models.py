"""Enrolment records for the factors an account can be challenged with."""

import uuid
from typing import Final

from django.conf import settings
from django.db import models
from django.utils import timezone

from infrastructure.oauth.core.crypto import decrypt_secret, encrypt_secret


class SecondFactorMethod:
    """The factor names, typed as plain strings. See :class:`~auth_core.models.AuthEventType`."""

    TOTP: Final = "totp"
    SMS: Final = "sms"
    EMAIL: Final = "email"
    RECOVERY: Final = "recovery"


class SecondFactor(models.Model):
    """One enrolled factor. At most one row per method per account.

    Enrolment is only real once ``confirmed_at`` is set: a user who scans a QR
    code but never proves they can read it must not be locked out of their own
    account on the next sign-in.
    """

    class Method(models.TextChoices):
        TOTP = SecondFactorMethod.TOTP, "Authenticator app"
        SMS = SecondFactorMethod.SMS, "SMS code"
        EMAIL = SecondFactorMethod.EMAIL, "Email code"
        RECOVERY = SecondFactorMethod.RECOVERY, "Recovery codes"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="second_factors",
    )
    method = models.CharField(max_length=16, choices=Method)
    destination = models.CharField(max_length=255, blank=True)
    secret_encrypted = models.TextField(blank=True, editable=False)
    last_counter = models.BigIntegerField(default=0)
    confirmed_at = models.DateTimeField(null=True, blank=True)
    last_used_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at"]
        constraints = [
            models.UniqueConstraint(fields=["user", "method"], name="auth_unique_second_factor")
        ]
        indexes = [models.Index(fields=["user", "confirmed_at"])]

    def __str__(self) -> str:
        return f"{self.user_id}:{self.method}"

    @property
    def is_confirmed(self) -> bool:
        return self.confirmed_at is not None

    def set_secret(self, value: str) -> None:
        self.secret_encrypted = encrypt_secret(value)

    def secret(self) -> str:
        return decrypt_secret(self.secret_encrypted)

    def confirm(self) -> None:
        self.confirmed_at = timezone.now()
        self.save(update_fields=["confirmed_at"])

    def mark_used(self, counter: int | None = None) -> None:
        self.last_used_at = timezone.now()
        fields = ["last_used_at"]
        if counter is not None:
            self.last_counter = counter
            fields.append("last_counter")
        self.save(update_fields=fields)


class RecoveryCode(models.Model):
    """A single-use way back in when every other factor is unavailable."""

    id = models.BigAutoField(primary_key=True)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="auth_recovery_codes",
    )
    code_hash = models.CharField(max_length=64, editable=False)
    used_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at"]
        constraints = [
            models.UniqueConstraint(fields=["user", "code_hash"], name="auth_unique_recovery_code")
        ]
        indexes = [models.Index(fields=["user", "used_at"])]

    def __str__(self) -> str:
        return f"{self.user_id}:{self.id}"
