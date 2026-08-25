"""Admin for linked Apple accounts."""

from django.contrib import admin

from infrastructure.oauth.apple.models import AppleAccount


@admin.register(AppleAccount)
class AppleAccountAdmin(admin.ModelAdmin):
    """Accounts linked through Apple.

    Provider tokens are stored encrypted and excluded here: the admin's job is to
    show which local account a provider identity belongs to, not to hand out a
    credential for calling the provider. ``subject`` is the provider's own
    immutable identifier and is what a link is really keyed on.
    """

    list_display = (
        "subject",
        "user",
        "email",
        "email_verified",
        "is_private_email",
        "last_login_at",
    )
    list_filter = ("email_verified", "is_private_email")
    search_fields = (
        "subject",
        "email",
        "user__username",
        "user__email",
    )
    autocomplete_fields = ("user",)
    exclude = ("access_token_encrypted", "refresh_token_encrypted")
    readonly_fields = ("id", "subject", "created_at", "updated_at", "last_login_at", "raw_claims")
    date_hierarchy = "created_at"
    ordering = ("-last_login_at",)
    list_select_related = ("user",)
