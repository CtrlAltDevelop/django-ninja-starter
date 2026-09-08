"""Admin for token families, their rotation history, and reuse reports."""

from django.contrib import admin
from unfold.admin import TabularInline

from infrastructure.common.admin import ReadOnlyAdmin, RevocableAdmin
from infrastructure.oauth.rotation.models import (
    RefreshTokenReuseEvent,
    RotatingAccessToken,
    RotatingRefreshToken,
    TokenFamily,
)


class RotatingRefreshTokenInline(TabularInline):
    """A family's rotation chain, in the order it happened."""

    model = RotatingRefreshToken
    fk_name = "family"
    extra = 0
    can_delete = False
    fields = ("rotation_index", "issued_at", "used_at", "expires_at", "reuse_detected_at")
    readonly_fields = fields
    ordering = ("rotation_index",)
    show_change_link = True

    def has_add_permission(self, request: object, obj: object = None) -> bool:
        return False


@admin.register(TokenFamily)
class TokenFamilyAdmin(RevocableAdmin):
    """One family per sign-in. Revoking it ends every token descended from it.

    ``reuse_detected_at`` is the column to watch: a value there means a spent
    refresh token came back, and the family was ended on suspicion of theft.
    """

    revoke_label = "token family"
    list_display = (
        "id",
        "user",
        "created_at",
        "last_rotated_at",
        "expires_at",
        "revoked_at",
        "reuse_detected_at",
    )
    list_filter = (
        "revocation_reason",
        ("reuse_detected_at", admin.EmptyFieldListFilter),
        ("revoked_at", admin.EmptyFieldListFilter),
    )
    search_fields = ("user__username", "user__email", "issued_ip", "device_id")
    date_hierarchy = "created_at"
    ordering = ("-created_at",)
    list_select_related = ("user",)
    inlines = (RotatingRefreshTokenInline,)


@admin.register(RotatingRefreshToken)
class RotatingRefreshTokenAdmin(ReadOnlyAdmin):
    """The rotation chain itself. Read-only: ancestry is a record, not a setting."""

    list_display = (
        "id",
        "user",
        "family",
        "rotation_index",
        "issued_at",
        "used_at",
        "reuse_detected_at",
    )
    list_filter = (
        "revocation_reason",
        ("used_at", admin.EmptyFieldListFilter),
        ("reuse_detected_at", admin.EmptyFieldListFilter),
    )
    search_fields = ("user__username", "user__email", "issued_ip")
    date_hierarchy = "issued_at"
    ordering = ("-issued_at",)
    list_select_related = ("user", "family")


@admin.register(RotatingAccessToken)
class RotatingAccessTokenAdmin(RevocableAdmin):
    revoke_label = "access token"
    list_display = ("id", "user", "family", "issued_at", "expires_at", "revoked_at")
    list_filter = ("revocation_reason", ("revoked_at", admin.EmptyFieldListFilter))
    search_fields = ("user__username", "user__email", "issued_ip")
    date_hierarchy = "issued_at"
    ordering = ("-issued_at",)
    list_select_related = ("user", "family")


@admin.register(RefreshTokenReuseEvent)
class RefreshTokenReuseEventAdmin(ReadOnlyAdmin):
    """Reports of a spent refresh token coming back.

    Each row is a family that was ended because two parties held the same token.
    Only a fingerprint of the digest is kept, which is enough to correlate
    repeated attempts without storing the credential itself.
    """

    list_display = ("detected_at", "family", "refresh_token", "presented_fingerprint", "ip_address")
    search_fields = ("family__user__username", "ip_address", "presented_fingerprint")
    date_hierarchy = "detected_at"
    ordering = ("-detected_at",)
    list_select_related = ("family", "refresh_token")
