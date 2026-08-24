"""Sliding tokens with idle and absolute expiration boundaries."""

from datetime import timedelta

from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone

from infrastructure.oauth_core.models import AbstractOAuthToken


class SlidingToken(AbstractOAuthToken):
    idle_timeout_seconds = models.PositiveIntegerField(default=900)
    absolute_expires_at = models.DateTimeField()
    previous_expires_at = models.DateTimeField(null=True, blank=True)
    slide_count = models.PositiveIntegerField(default=0)

    class Meta(AbstractOAuthToken.Meta):
        verbose_name = "sliding token"
        indexes = [
            models.Index(fields=["user", "expires_at"]),
            models.Index(fields=["absolute_expires_at", "revoked_at"]),
        ]

    def clean(self) -> None:
        if self.expires_at > self.absolute_expires_at:
            raise ValidationError("Sliding expiry cannot exceed absolute expiry")

    @property
    def is_active(self) -> bool:
        now = timezone.now()
        return super().is_active and self.absolute_expires_at > now

    def slide(self) -> bool:
        """Advance idle expiry without crossing the absolute lifetime."""
        if not self.is_active:
            return False
        now = timezone.now()
        self.previous_expires_at = self.expires_at
        self.expires_at = min(
            now + timedelta(seconds=self.idle_timeout_seconds),
            self.absolute_expires_at,
        )
        self.last_used_at = now
        self.slide_count += 1
        self.save(
            update_fields=[
                "previous_expires_at",
                "expires_at",
                "last_used_at",
                "slide_count",
            ]
        )
        return True


class SlidingTokenEvent(models.Model):
    class EventType(models.TextChoices):
        ISSUED = "issued", "Issued"
        SLID = "slid", "Expiry extended"
        REVOKED = "revoked", "Revoked"
        EXPIRED = "expired", "Expired"

    id = models.BigAutoField(primary_key=True)
    token = models.ForeignKey(SlidingToken, on_delete=models.CASCADE, related_name="events")
    event_type = models.CharField(max_length=16, choices=EventType)
    old_expires_at = models.DateTimeField(null=True, blank=True)
    new_expires_at = models.DateTimeField(null=True, blank=True)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"{self.event_type}:{self.token_id}"
