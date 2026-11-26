"""The admin: endpoints, and the delivery record (read-only, because it is a record)."""

from typing import Any

from django.contrib import admin
from django.http import HttpRequest

from apps.webhooks.models import Delivery, DeliveryAttempt, Endpoint

try:  # pragma: no cover - exercised by whichever branch the project installs
    from unfold.admin import ModelAdmin, TabularInline
except ImportError:  # pragma: no cover - only in a project without Unfold
    from django.contrib.admin import ModelAdmin, TabularInline


@admin.register(Endpoint)
class EndpointAdmin(ModelAdmin):
    list_display = ("url", "owner", "is_active", "created_at")
    list_filter = ("is_active",)
    search_fields = ("url",)
    exclude = ("secret",)


class AttemptInline(TabularInline):
    model = DeliveryAttempt
    extra = 0
    can_delete = False
    readonly_fields = ("number", "status_code", "error", "duration_ms", "created_at")

    def has_add_permission(self, request: HttpRequest, obj: Any = None) -> bool:
        return False


@admin.register(Delivery)
class DeliveryAdmin(ModelAdmin):
    list_display = ("event", "endpoint", "status", "attempts", "next_attempt_at", "created_at")
    list_filter = ("status",)
    readonly_fields = ("endpoint", "event", "payload", "status", "attempts", "next_attempt_at")
    inlines = (AttemptInline,)

    def has_add_permission(self, request: HttpRequest) -> bool:
        return False
