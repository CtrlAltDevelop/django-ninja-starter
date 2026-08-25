"""Admin bases for rows the application owns and a person should not rewrite.

Two kinds of table here resist ordinary admin editing.

An *audit* row is a claim about something that happened. Letting it be added,
edited or deleted through a form would make the whole trail worthless as
evidence -- the useful property of a login log is precisely that nobody can go
back and adjust it. So those are registered read-only.

A *credential* row is not editable either, but for a different reason: its only
secret is a digest, so there is nothing meaningful to change, while there is
something very meaningful to *do* -- revoke it. Hence a base that forbids
editing and offers that one action instead.
"""

from typing import Any

from django.contrib import admin, messages
from django.db.models import Model, QuerySet
from django.http import HttpRequest

ADMIN_REASON = "admin"


class ReadOnlyAdmin(admin.ModelAdmin):
    """A table the admin may look at and nothing more."""

    def get_readonly_fields(self, request: HttpRequest, obj: Model | None = None) -> list[str]:
        return [field.name for field in self.model._meta.fields]

    def has_add_permission(self, request: HttpRequest) -> bool:
        return False

    def has_change_permission(self, request: HttpRequest, obj: Model | None = None) -> bool:
        return False

    def has_delete_permission(self, request: HttpRequest, obj: Model | None = None) -> bool:
        return False


class RevocableAdmin(ReadOnlyAdmin):
    """A credential table: not editable, but revocable.

    Subclasses set :attr:`revoke_label` to whatever the row is called, so the
    action reads sensibly in the dropdown.
    """

    revoke_label = "credential"
    actions = ("revoke_selected",)

    @admin.action(description="Revoke the selected records")
    def revoke_selected(self, request: HttpRequest, queryset: QuerySet[Any]) -> None:
        revoked = 0
        for record in queryset:
            if getattr(record, "revoked_at", None) is None:
                record.revoke(ADMIN_REASON)
                revoked += 1
        self.message_user(
            request,
            f"Revoked {revoked} {self.revoke_label}(s). "
            f"{queryset.count() - revoked} were already revoked.",
            messages.SUCCESS,
        )
