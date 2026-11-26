"""The admin: who uploaded what. Read-only, because a file's metadata is a record
of what was sniffed and stored, and editing it by hand would make it a lie.
Deleting a row deletes its bytes (see the model's ``post_delete``)."""

from typing import Any

from django.contrib import admin
from django.http import HttpRequest

from apps.files.models import StoredFile

try:  # pragma: no cover - exercised by whichever branch the project installs
    from unfold.admin import ModelAdmin
except ImportError:  # pragma: no cover - only in a project without Unfold
    from django.contrib.admin import ModelAdmin


@admin.register(StoredFile)
class StoredFileAdmin(ModelAdmin):
    list_display = ("name", "owner", "content_type", "size", "created_at")
    list_filter = ("content_type",)
    search_fields = ("name", "owner__username", "owner__email")
    readonly_fields = ("id", "owner", "name", "storage_key", "content_type", "size", "created_at")

    def has_add_permission(self, request: HttpRequest) -> bool:
        return False

    def has_change_permission(self, request: HttpRequest, obj: Any = None) -> bool:
        return False
