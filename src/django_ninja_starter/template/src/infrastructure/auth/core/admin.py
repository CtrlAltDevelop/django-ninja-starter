"""Admin for the records every login method shares."""

from django.contrib import admin
from unfold.admin import ModelAdmin as UnfoldModelAdmin

from infrastructure.auth.core.models import AuthEvent, PhoneNumber
from infrastructure.common.admin import ReadOnlyAdmin


@admin.register(PhoneNumber)
class PhoneNumberAdmin(UnfoldModelAdmin):
    """Numbers an account has proven it controls.

    ``is_verified`` is editable on purpose: support occasionally has to confirm a
    number out of band when a carrier will not deliver. Everything else about the
    row is a fact of how it was created.
    """

    list_display = ("number", "user", "is_verified", "is_primary", "created_at", "verified_at")
    list_filter = ("is_verified", "is_primary")
    search_fields = ("number", "user__username", "user__email")
    autocomplete_fields = ("user",)
    readonly_fields = ("id", "created_at", "verified_at")
    date_hierarchy = "created_at"
    ordering = ("-created_at",)


@admin.register(AuthEvent)
class AuthEventAdmin(ReadOnlyAdmin):
    """The sign-in audit trail. Read-only: a log nobody can edit is the point.

    Identifiers are stored as digests, so searching is by account rather than by
    address -- the trail can answer "how many failures hit this address" without
    being a second copy of the user table.
    """

    list_display = ("created_at", "event_type", "method", "user", "ip_address")
    list_filter = ("event_type", "method")
    search_fields = ("user__username", "user__email", "ip_address")
    date_hierarchy = "created_at"
    ordering = ("-created_at",)
    list_select_related = ("user",)
