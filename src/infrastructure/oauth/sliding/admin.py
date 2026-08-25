"""Admin for sliding tokens and their expiry history."""

from django.contrib import admin

from infrastructure.common.admin import ReadOnlyAdmin, RevocableAdmin
from infrastructure.oauth.sliding.models import SlidingToken, SlidingTokenEvent


@admin.register(SlidingToken)
class SlidingTokenAdmin(RevocableAdmin):
    """Live sliding tokens, with the one action that matters: revoke."""

    revoke_label = "token"
    list_display = (
        "id",
        "user",
        "issued_at",
        "expires_at",
        "absolute_expires_at",
        "slide_count",
        "revoked_at",
    )
    list_filter = ("revocation_reason", ("revoked_at", admin.EmptyFieldListFilter))
    search_fields = ("user__username", "user__email", "issued_ip")
    date_hierarchy = "issued_at"
    ordering = ("-issued_at",)
    list_select_related = ("user",)


@admin.register(SlidingTokenEvent)
class SlidingTokenEventAdmin(ReadOnlyAdmin):
    """Every extension, revocation and expiry of a token. Read-only."""

    list_display = ("created_at", "event_type", "token", "old_expires_at", "new_expires_at")
    list_filter = ("event_type",)
    search_fields = ("token__user__username", "ip_address")
    date_hierarchy = "created_at"
    ordering = ("-created_at",)
    list_select_related = ("token",)
