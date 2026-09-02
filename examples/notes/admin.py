"""Admin for notes.

``autocomplete_fields`` on the owner is what keeps this page usable once there
are more accounts than a dropdown can hold. It works because the accounts admin
declares ``search_fields`` -- Django refuses the autocomplete otherwise.
"""

from django.contrib import admin

from apps.notes.models import Note


@admin.register(Note)
class NoteAdmin(admin.ModelAdmin):
    list_display = ("title", "owner", "pinned", "updated_at")
    list_filter = ("pinned", "created_at")
    search_fields = ("title", "body", "owner__username")
    autocomplete_fields = ("owner",)
    readonly_fields = ("id", "created_at", "updated_at")
    date_hierarchy = "updated_at"
