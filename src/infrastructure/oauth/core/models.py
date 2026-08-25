"""Models shared by every supported OAuth token mode."""

import uuid
from typing import Any

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone


def default_grant_types() -> list[str]:
    return ["authorization_code"]


def default_response_types() -> list[str]:
    return ["code"]


class OAuthScope(models.Model):
    name = models.CharField(max_length=100, primary_key=True)
    description = models.TextField(blank=True)
    is_default = models.BooleanField(default=False)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["name"]

    def __str__(self) -> str:
        return self.name


class OAuthClient(models.Model):
    class ClientType(models.TextChoices):
        PUBLIC = "public", "Public"
        CONFIDENTIAL = "confidential", "Confidential"

    class TokenEndpointAuthMethod(models.TextChoices):
        NONE = "none", "None"
        CLIENT_SECRET_BASIC = "client_secret_basic", "Client secret basic"
        CLIENT_SECRET_POST = "client_secret_post", "Client secret post"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=200)
    client_id = models.CharField(max_length=128, unique=True, db_index=True)
    client_secret_hash = models.CharField(max_length=128, blank=True, editable=False)
    client_type = models.CharField(max_length=20, choices=ClientType, default=ClientType.PUBLIC)
    token_endpoint_auth_method = models.CharField(
        max_length=32,
        choices=TokenEndpointAuthMethod,
        default=TokenEndpointAuthMethod.NONE,
    )
    redirect_uris = models.JSONField(default=list)
    grant_types = models.JSONField(default=default_grant_types)
    response_types = models.JSONField(default=default_response_types)
    audiences = models.JSONField(default=list)
    scopes = models.ManyToManyField(OAuthScope, blank=True, related_name="clients")
    is_first_party = models.BooleanField(default=False)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name"]

    def __str__(self) -> str:
        return self.name

    def clean(self) -> None:
        if self.client_type == self.ClientType.CONFIDENTIAL and not self.client_secret_hash:
            raise ValidationError("Confidential clients require a hashed client secret")
        if self.client_type == self.ClientType.PUBLIC and self.client_secret_hash:
            raise ValidationError("Public clients must not have a client secret")


class OAuthConsent(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="oauth_consents",
    )
    client = models.ForeignKey(OAuthClient, on_delete=models.CASCADE, related_name="consents")
    scopes = models.ManyToManyField(OAuthScope, related_name="consents")
    granted_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    expires_at = models.DateTimeField(null=True, blank=True)
    revoked_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["user", "client"], name="oauth_unique_consent")
        ]

    def __str__(self) -> str:
        return f"{self.user_id}:{self.client_id}"

    @property
    def is_active(self) -> bool:
        now = timezone.now()
        return self.revoked_at is None and (self.expires_at is None or self.expires_at > now)


class OAuthAuthorizationCode(models.Model):
    class CodeChallengeMethod(models.TextChoices):
        S256 = "S256", "SHA-256"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    code_hash = models.CharField(max_length=64, unique=True, editable=False)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="oauth_authorization_codes",
    )
    client = models.ForeignKey(
        OAuthClient,
        on_delete=models.CASCADE,
        related_name="authorization_codes",
    )
    redirect_uri = models.TextField()
    scopes = models.JSONField(default=list)
    audience = models.CharField(max_length=255, blank=True)
    code_challenge = models.CharField(max_length=128)
    code_challenge_method = models.CharField(
        max_length=10,
        choices=CodeChallengeMethod,
        default=CodeChallengeMethod.S256,
    )
    nonce = models.CharField(max_length=255, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField()
    consumed_at = models.DateTimeField(null=True, blank=True)
    revoked_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        indexes = [models.Index(fields=["client", "expires_at"])]

    def __str__(self) -> str:
        return str(self.id)

    @property
    def is_active(self) -> bool:
        return (
            self.consumed_at is None
            and self.revoked_at is None
            and self.expires_at > timezone.now()
        )


class OAuthAuditEvent(models.Model):
    class EventType(models.TextChoices):
        CLIENT_CREATED = "client_created", "Client created"
        CONSENT_GRANTED = "consent_granted", "Consent granted"
        CONSENT_REVOKED = "consent_revoked", "Consent revoked"
        CODE_ISSUED = "code_issued", "Authorization code issued"
        TOKEN_ISSUED = "token_issued", "Token issued"
        TOKEN_REFRESHED = "token_refreshed", "Token refreshed"
        TOKEN_REVOKED = "token_revoked", "Token revoked"
        TOKEN_REUSE = "token_reuse", "Token reuse detected"
        AUTH_FAILURE = "auth_failure", "Authentication failure"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    event_type = models.CharField(max_length=32, choices=EventType)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="oauth_audit_events",
    )
    client = models.ForeignKey(
        OAuthClient,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="audit_events",
    )
    token_fingerprint = models.CharField(max_length=16, blank=True)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    user_agent = models.TextField(blank=True)
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["event_type", "created_at"])]

    def __str__(self) -> str:
        return f"{self.event_type}:{self.id}"


class AbstractOAuthToken(models.Model):
    class RevocationReason(models.TextChoices):
        LOGOUT = "logout", "Logout"
        ADMIN = "admin", "Administrator"
        EXPIRED = "expired", "Expired"
        ROTATED = "rotated", "Rotated"
        REUSE = "reuse", "Reuse detected"
        PASSWORD_CHANGED = "password_changed", "Password changed"
        SECURITY_EVENT = "security_event", "Security event"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="%(app_label)s_%(class)s_tokens",
    )
    client = models.ForeignKey(
        OAuthClient,
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="%(app_label)s_%(class)s_tokens",
    )
    token_hash = models.CharField(max_length=64, unique=True, editable=False)
    scopes = models.JSONField(default=list)
    audience = models.CharField(max_length=255, blank=True)
    issued_at = models.DateTimeField(default=timezone.now, editable=False)
    expires_at = models.DateTimeField()
    last_used_at = models.DateTimeField(null=True, blank=True)
    revoked_at = models.DateTimeField(null=True, blank=True)
    revocation_reason = models.CharField(max_length=32, choices=RevocationReason, blank=True)
    issued_ip = models.GenericIPAddressField(null=True, blank=True)
    user_agent = models.TextField(blank=True)
    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        abstract = True
        indexes = [models.Index(fields=["user", "expires_at"])]

    @property
    def is_active(self) -> bool:
        return self.revoked_at is None and self.expires_at > timezone.now()

    def revoke(self, reason: str = "logout", **save_kwargs: Any) -> None:
        self.revoked_at = timezone.now()
        self.revocation_reason = reason
        self.save(update_fields=["revoked_at", "revocation_reason"], **save_kwargs)


class SocialLoginAttempt(models.Model):
    """Single-use state for an outbound social authorization-code flow."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    provider = models.CharField(max_length=32, db_index=True)
    state_hash = models.CharField(max_length=64, unique=True, editable=False)
    binding_hash = models.CharField(max_length=64, blank=True, editable=False)
    nonce_hash = models.CharField(max_length=64, blank=True, editable=False)
    code_verifier_encrypted = models.TextField(blank=True, editable=False)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="oauth_social_login_attempts",
    )
    redirect_uri = models.URLField(max_length=500)
    next_url = models.CharField(max_length=500, default="/")
    requested_scopes = models.JSONField(default=list)
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField()
    consumed_at = models.DateTimeField(null=True, blank=True)
    error = models.CharField(max_length=255, blank=True)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    user_agent = models.TextField(blank=True)

    class Meta:
        indexes = [models.Index(fields=["provider", "expires_at"])]

    def __str__(self) -> str:
        return f"{self.provider}:{self.id}"

    @property
    def is_active(self) -> bool:
        return self.consumed_at is None and self.expires_at > timezone.now()


class AbstractSocialAccount(models.Model):
    """Provider-owned identity linked to a local Django user."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="%(app_label)s_%(class)s_accounts",
    )
    subject = models.CharField(max_length=255, unique=True)
    email = models.EmailField(blank=True)
    email_verified = models.BooleanField(default=False)
    display_name = models.CharField(max_length=255, blank=True)
    avatar_url = models.URLField(max_length=1000, blank=True)
    scopes = models.JSONField(default=list)
    access_token_encrypted = models.TextField(blank=True, editable=False)
    refresh_token_encrypted = models.TextField(blank=True, editable=False)
    token_expires_at = models.DateTimeField(null=True, blank=True)
    raw_claims = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    last_login_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        abstract = True
        indexes = [models.Index(fields=["user", "email"])]

    def __str__(self) -> str:
        return self.email or self.subject

    def access_token(self) -> str:
        from infrastructure.oauth.core.crypto import decrypt_secret

        return decrypt_secret(self.access_token_encrypted)

    def refresh_token(self) -> str:
        from infrastructure.oauth.core.crypto import decrypt_secret

        return decrypt_secret(self.refresh_token_encrypted)
