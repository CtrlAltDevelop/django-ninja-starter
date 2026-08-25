"""Admin for server-side sessions and their access tokens."""

from django.contrib import admin

from infrastructure.common.admin import ReadOnlyAdmin, RevocableAdmin
from infrastructure.oauth.session.models import (
    OAuthSession,
    SessionAccessToken,
    SessionRevocation,
)


class SessionAccessTokenInline(admin.TabularInline):
    """The tokens a session has minted, shown where they make sense: under it."""

    model = SessionAccessToken
    extra = 0
    can_delete = False
    fields = ("issued_at", "expires_at", "revoked_at", "revocation_reason", "issued_ip")
    readonly_fields = fields
    show_change_link = True

    def has_add_permission(self, request: object, obj: object = None) -> bool:
        return False


@admin.register(OAuthSession)
class OAuthSessionAdmin(RevocableAdmin):
    """Live sessions. Revoking one retires every access token under it."""

    revoke_label = "session"
    list_display = ("id", "user", "created_at", "last_seen_at", "expires_at", "revoked_at")
    list_filter = ("revocation_reason", ("revoked_at", admin.EmptyFieldListFilter))
    search_fields = ("user__username", "user__email", "ip_address", "device_id")
    date_hierarchy = "created_at"
    ordering = ("-created_at",)
    list_select_related = ("user",)
    inlines = (SessionAccessTokenInline,)


@admin.register(SessionAccessToken)
class SessionAccessTokenAdmin(RevocableAdmin):
    """Individually revocable access tokens, which is this mode's whole point."""

    revoke_label = "access token"
    list_display = ("id", "user", "session", "issued_at", "expires_at", "revoked_at")
    list_filter = ("revocation_reason", ("revoked_at", admin.EmptyFieldListFilter))
    search_fields = ("user__username", "user__email", "issued_ip")
    date_hierarchy = "issued_at"
    ordering = ("-issued_at",)
    list_select_related = ("user", "session")


@admin.register(SessionRevocation)
class SessionRevocationAdmin(ReadOnlyAdmin):
    """Who ended which session, and how many tokens it cost. Read-only."""

    list_display = ("created_at", "session", "reason", "access_tokens_revoked", "revoked_by")
    list_filter = ("reason",)
    search_fields = ("session__user__username", "revoked_by__username")
    date_hierarchy = "created_at"
    ordering = ("-created_at",)
    list_select_related = ("session", "revoked_by")
