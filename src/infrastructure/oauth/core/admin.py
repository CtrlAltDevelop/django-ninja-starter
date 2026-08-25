"""Admin for OAuth clients, scopes, consents, and the audit trail."""

from django.contrib import admin

from infrastructure.common.admin import ReadOnlyAdmin
from infrastructure.oauth.core.models import (
    OAuthAuditEvent,
    OAuthAuthorizationCode,
    OAuthClient,
    OAuthConsent,
    OAuthScope,
    SocialLoginAttempt,
)


@admin.register(OAuthScope)
class OAuthScopeAdmin(admin.ModelAdmin):
    list_display = ("name", "is_default", "is_active", "description")
    list_filter = ("is_default", "is_active")
    search_fields = ("name", "description")
    ordering = ("name",)


@admin.register(OAuthClient)
class OAuthClientAdmin(admin.ModelAdmin):
    """Registered clients. The client secret is stored hashed and never shown."""

    list_display = (
        "name",
        "client_id",
        "client_type",
        "is_first_party",
        "is_active",
        "created_at",
    )
    list_filter = ("client_type", "token_endpoint_auth_method", "is_first_party", "is_active")
    search_fields = ("name", "client_id")
    filter_horizontal = ("scopes",)
    exclude = ("client_secret_hash",)
    readonly_fields = ("id", "created_at", "updated_at")
    date_hierarchy = "created_at"
    ordering = ("name",)


@admin.register(OAuthConsent)
class OAuthConsentAdmin(admin.ModelAdmin):
    """What a user has agreed a client may do on their behalf."""

    list_display = ("user", "client", "granted_at", "expires_at", "revoked_at")
    list_filter = ("client", ("revoked_at", admin.EmptyFieldListFilter))
    search_fields = ("user__username", "user__email", "client__name")
    autocomplete_fields = ("user",)
    filter_horizontal = ("scopes",)
    readonly_fields = ("id", "granted_at", "updated_at")
    date_hierarchy = "granted_at"
    ordering = ("-granted_at",)
    list_select_related = ("user", "client")


@admin.register(OAuthAuthorizationCode)
class OAuthAuthorizationCodeAdmin(ReadOnlyAdmin):
    """Codes in flight. Read-only, and short-lived by design."""

    list_display = ("client", "user", "created_at", "expires_at", "consumed_at", "revoked_at")
    list_filter = ("client", ("consumed_at", admin.EmptyFieldListFilter))
    search_fields = ("user__username", "client__name")
    date_hierarchy = "created_at"
    ordering = ("-created_at",)
    list_select_related = ("user", "client")


@admin.register(OAuthAuditEvent)
class OAuthAuditEventAdmin(ReadOnlyAdmin):
    """The OAuth audit trail. Read-only: a log nobody can edit is the point."""

    list_display = ("created_at", "event_type", "client", "user", "ip_address")
    list_filter = ("event_type", "client")
    search_fields = ("user__username", "client__name", "ip_address")
    date_hierarchy = "created_at"
    ordering = ("-created_at",)
    list_select_related = ("user", "client")


@admin.register(SocialLoginAttempt)
class SocialLoginAttemptAdmin(ReadOnlyAdmin):
    """Social logins in flight, and why the failed ones failed.

    Read-only, and deliberately worth keeping: ``error`` is where a
    misconfigured provider or a tampered callback shows up.
    """

    list_display = ("provider", "created_at", "expires_at", "consumed_at", "user", "error")
    list_filter = ("provider", ("consumed_at", admin.EmptyFieldListFilter))
    search_fields = ("provider", "user__username", "ip_address", "error")
    date_hierarchy = "created_at"
    ordering = ("-created_at",)
    list_select_related = ("user",)
