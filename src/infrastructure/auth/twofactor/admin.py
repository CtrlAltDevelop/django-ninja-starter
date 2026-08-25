"""Admin for second-factor enrolments and recovery codes."""

from django.contrib import admin

from infrastructure.auth.twofactor.models import RecoveryCode, SecondFactor
from infrastructure.common.admin import ReadOnlyAdmin


@admin.register(SecondFactor)
class SecondFactorAdmin(admin.ModelAdmin):
    """Enrolled factors.

    The shared secret is never shown or editable -- it is stored encrypted and an
    authenticator app is the only thing that needs it. Deleting a row is
    supported, because that is how support removes a factor for somebody who has
    lost their device.
    """

    list_display = ("user", "method", "destination", "is_confirmed", "created_at", "last_used_at")
    list_filter = ("method", ("confirmed_at", admin.EmptyFieldListFilter))
    search_fields = ("user__username", "user__email", "destination")
    autocomplete_fields = ("user",)
    exclude = ("secret_encrypted",)
    readonly_fields = ("id", "created_at", "confirmed_at", "last_used_at", "last_counter")
    date_hierarchy = "created_at"
    ordering = ("-created_at",)
    list_select_related = ("user",)

    @admin.display(boolean=True, description="Confirmed")
    def is_confirmed(self, obj: SecondFactor) -> bool:
        return obj.is_confirmed


@admin.register(RecoveryCode)
class RecoveryCodeAdmin(ReadOnlyAdmin):
    """Recovery codes, as digests.

    Read-only because there is nothing here a person could usefully change: the
    clear code was shown once and never stored. Issuing a replacement set is done
    through the API, which retires the old ones as a unit.
    """

    list_display = ("user", "created_at", "used_at")
    list_filter = (("used_at", admin.EmptyFieldListFilter),)
    search_fields = ("user__username", "user__email")
    date_hierarchy = "created_at"
    ordering = ("-created_at",)
    list_select_related = ("user",)
