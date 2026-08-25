"""Server-side sessions and independently revocable access tokens."""

import uuid

from django.conf import settings
from django.db import models
from django.utils import timezone

from infrastructure.oauth.core.models import AbstractOAuthToken, OAuthClient


class OAuthSession(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="oauth_sessions",
    )
    client = models.ForeignKey(
        OAuthClient,
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="sessions",
    )
    session_key_hash = models.CharField(max_length=64, unique=True, editable=False)
    device_id = models.CharField(max_length=255, blank=True)
    scopes = models.JSONField(default=list)
    audience = models.CharField(max_length=255, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    last_seen_at = models.DateTimeField(default=timezone.now)
    expires_at = models.DateTimeField()
    revoked_at = models.DateTimeField(null=True, blank=True)
    revocation_reason = models.CharField(max_length=255, blank=True)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    user_agent = models.TextField(blank=True)
    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        indexes = [
            models.Index(fields=["user", "revoked_at"]),
            models.Index(fields=["expires_at"]),
        ]

    def __str__(self) -> str:
        return str(self.id)

    @property
    def is_active(self) -> bool:
        return self.revoked_at is None and self.expires_at > timezone.now()

    def revoke(self, reason: str = "logout") -> None:
        now = timezone.now()
        self.revoked_at = now
        self.revocation_reason = reason
        self.save(update_fields=["revoked_at", "revocation_reason"])
        self.access_tokens.filter(revoked_at__isnull=True).update(
            revoked_at=now,
            revocation_reason=AbstractOAuthToken.RevocationReason.LOGOUT,
        )


class SessionAccessToken(AbstractOAuthToken):
    session = models.ForeignKey(
        OAuthSession,
        on_delete=models.CASCADE,
        related_name="access_tokens",
    )

    class Meta(AbstractOAuthToken.Meta):
        verbose_name = "session access token"
        indexes = [
            models.Index(fields=["user", "expires_at"]),
            models.Index(fields=["session", "revoked_at"]),
        ]

    @property
    def is_active(self) -> bool:
        return super().is_active and self.session.is_active


class SessionRevocation(models.Model):
    id = models.BigAutoField(primary_key=True)
    session = models.ForeignKey(OAuthSession, on_delete=models.CASCADE, related_name="revocations")
    revoked_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="oauth_session_revocations",
    )
    reason = models.CharField(max_length=255)
    access_tokens_revoked = models.PositiveIntegerField(default=0)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"{self.session_id}:{self.reason}"
