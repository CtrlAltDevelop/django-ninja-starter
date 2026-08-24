"""Access/refresh pairs with rotation ancestry and replay detection."""

import uuid

from django.conf import settings
from django.db import models
from django.utils import timezone

from infrastructure.oauth_core.models import AbstractOAuthToken, OAuthClient


class TokenFamily(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="oauth_token_families",
    )
    client = models.ForeignKey(
        OAuthClient,
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="token_families",
    )
    scopes = models.JSONField(default=list)
    audience = models.CharField(max_length=255, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    last_rotated_at = models.DateTimeField(null=True, blank=True)
    expires_at = models.DateTimeField()
    revoked_at = models.DateTimeField(null=True, blank=True)
    revocation_reason = models.CharField(max_length=255, blank=True)
    reuse_detected_at = models.DateTimeField(null=True, blank=True)
    device_id = models.CharField(max_length=255, blank=True)
    issued_ip = models.GenericIPAddressField(null=True, blank=True)
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
        return (
            self.revoked_at is None
            and self.reuse_detected_at is None
            and self.expires_at > timezone.now()
        )

    def revoke(self, reason: str = "logout", *, reuse_detected: bool = False) -> None:
        now = timezone.now()
        self.revoked_at = now
        self.revocation_reason = reason
        fields = ["revoked_at", "revocation_reason"]
        if reuse_detected:
            self.reuse_detected_at = now
            fields.append("reuse_detected_at")
        self.save(update_fields=fields)
        self.access_tokens.filter(revoked_at__isnull=True).update(
            revoked_at=now,
            revocation_reason=AbstractOAuthToken.RevocationReason.REUSE
            if reuse_detected
            else AbstractOAuthToken.RevocationReason.LOGOUT,
        )
        self.refresh_tokens.filter(revoked_at__isnull=True).update(
            revoked_at=now,
            revocation_reason=AbstractOAuthToken.RevocationReason.REUSE
            if reuse_detected
            else AbstractOAuthToken.RevocationReason.LOGOUT,
        )


class RotatingRefreshToken(AbstractOAuthToken):
    family = models.ForeignKey(TokenFamily, on_delete=models.CASCADE, related_name="refresh_tokens")
    parent = models.ForeignKey(
        "self",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="children",
    )
    replaced_by = models.OneToOneField(
        "self",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="replaces",
    )
    rotation_index = models.PositiveIntegerField(default=0)
    used_at = models.DateTimeField(null=True, blank=True)
    reuse_detected_at = models.DateTimeField(null=True, blank=True)

    class Meta(AbstractOAuthToken.Meta):
        verbose_name = "rotating refresh token"
        constraints = [
            models.UniqueConstraint(
                fields=["family", "rotation_index"],
                name="oauth_unique_refresh_rotation",
            )
        ]
        indexes = [
            models.Index(fields=["user", "expires_at"]),
            models.Index(fields=["family", "rotation_index"]),
        ]

    @property
    def is_active(self) -> bool:
        return super().is_active and self.used_at is None and self.family.is_active

    def mark_used(self, replacement: "RotatingRefreshToken") -> None:
        self.used_at = timezone.now()
        self.replaced_by = replacement
        self.revoked_at = self.used_at
        self.revocation_reason = self.RevocationReason.ROTATED
        self.save(update_fields=["used_at", "replaced_by", "revoked_at", "revocation_reason"])
        self.family.last_rotated_at = self.used_at
        self.family.save(update_fields=["last_rotated_at"])

    def mark_reuse(self) -> None:
        self.reuse_detected_at = timezone.now()
        self.save(update_fields=["reuse_detected_at"])
        self.family.revoke("refresh_token_reuse", reuse_detected=True)


class RotatingAccessToken(AbstractOAuthToken):
    family = models.ForeignKey(TokenFamily, on_delete=models.CASCADE, related_name="access_tokens")
    issued_from = models.ForeignKey(
        RotatingRefreshToken,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="access_tokens",
    )

    class Meta(AbstractOAuthToken.Meta):
        verbose_name = "rotating access token"
        indexes = [
            models.Index(fields=["user", "expires_at"]),
            models.Index(fields=["family", "revoked_at"]),
        ]

    @property
    def is_active(self) -> bool:
        return super().is_active and self.family.is_active


class RefreshTokenReuseEvent(models.Model):
    id = models.BigAutoField(primary_key=True)
    family = models.ForeignKey(TokenFamily, on_delete=models.CASCADE, related_name="reuse_events")
    refresh_token = models.ForeignKey(
        RotatingRefreshToken,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="reuse_events",
    )
    presented_fingerprint = models.CharField(max_length=16, blank=True)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    user_agent = models.TextField(blank=True)
    metadata = models.JSONField(default=dict, blank=True)
    detected_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-detected_at"]

    def __str__(self) -> str:
        return f"reuse:{self.family_id}:{self.id}"
