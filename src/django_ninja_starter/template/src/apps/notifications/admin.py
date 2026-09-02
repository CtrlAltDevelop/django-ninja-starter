"""The admin: writing a notification, and seeing who has read one.

Two screens, because there are two audiences for them. Somebody announcing a
release writes a notification, picks whether it is for one account or for
everybody, and saves -- and every connected socket has it before the page has
finished reloading, because saving is what broadcasts. Somebody asking "did they
see it?" reads the receipts.

Receipts are read-only. They are a record of something that happened, and a
record that can be typed in by hand is not a record.

The theme is resolved the way the CMS resolves its own: Unfold where the project
installs it, Django's own admin where it does not, so this app can be lifted
into a project that has never heard of either.
"""

from typing import Any

from django.contrib import admin
from django.db.models import Count, QuerySet
from django.http import HttpRequest
from django.utils.html import format_html

from apps.notifications.models import Audience, Notification, NotificationReceipt

try:  # pragma: no cover - exercised by whichever branch the project installs
    from unfold.admin import ModelAdmin
except ImportError:  # pragma: no cover - only in a project without Unfold
    from django.contrib.admin import ModelAdmin

LEVEL_COLOURS = {
    "info": "#2563eb",
    "success": "#16a34a",
    "warning": "#d97706",
    "error": "#dc2626",
}


@admin.register(Notification)
class NotificationAdmin(ModelAdmin):
    """Write one, and see how far it got."""

    list_display = (
        "subject",
        "audience_label",
        "recipient",
        "level_badge",
        "read_by",
        "created_at",
    )
    list_filter = ("audience", "level", "created_at")
    search_fields = ("subject", "body", "recipient__username", "recipient__email")
    autocomplete_fields = ("recipient",)
    date_hierarchy = "created_at"
    readonly_fields = ("id", "created_at")
    fieldsets = (
        (None, {"fields": ("audience", "recipient", "subject", "body", "level", "link")}),
        (
            "Payload",
            {
                "classes": ("collapse",),
                "description": "Sent to the client verbatim. Use it for ids the UI needs.",
                "fields": ("data",),
            },
        ),
        ("Record", {"classes": ("collapse",), "fields": ("id", "created_at")}),
    )

    def get_queryset(self, request: HttpRequest) -> QuerySet[Notification]:
        """Count receipts in the list query rather than once per row."""
        return (
            super()
            .get_queryset(request)
            .select_related("recipient")
            .annotate(receipt_count=Count("receipts"))
        )

    @admin.display(description="Audience", ordering="audience")
    def audience_label(self, notification: Notification) -> str:
        return "Everyone" if notification.is_global else "One account"

    @admin.display(description="Level", ordering="level")
    def level_badge(self, notification: Notification) -> Any:
        return format_html(
            '<span style="color: {}; font-weight: 600">{}</span>',
            LEVEL_COLOURS.get(notification.level, "#6b7280"),
            notification.get_level_display(),
        )

    @admin.display(description="Read by", ordering="receipt_count")
    def read_by(self, notification: Notification) -> str:
        """How many accounts have read it.

        For a broadcast that is the whole answer available: there is no roster of
        who was meant to receive it, so a percentage would be invented.
        """
        count = getattr(notification, "receipt_count", 0)
        if notification.audience == Audience.USER:
            return "Read" if count else "Unread"
        return f"{count} account{'' if count == 1 else 's'}"


@admin.register(NotificationReceipt)
class NotificationReceiptAdmin(ModelAdmin):
    """Who read what, and when. A log, so nothing here is editable."""

    # Enforced by the two methods below; declared here so the generated
    # documentation can say so without inheriting this project's ReadOnlyAdmin,
    # which imports Unfold directly and would not travel with this app.
    read_only_admin = True

    list_display = ("notification", "user", "read_at")
    list_filter = ("read_at",)
    search_fields = ("notification__subject", "user__username", "user__email")
    date_hierarchy = "read_at"

    def get_queryset(self, request: HttpRequest) -> QuerySet[NotificationReceipt]:
        return super().get_queryset(request).select_related("notification", "user")

    def has_add_permission(self, request: HttpRequest) -> bool:
        return False

    def has_change_permission(self, request: HttpRequest, obj: Any = None) -> bool:
        return False
