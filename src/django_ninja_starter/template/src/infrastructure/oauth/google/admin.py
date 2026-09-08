"""Admin for linked Google accounts."""

from django.contrib import admin
from unfold.admin import ModelAdmin as UnfoldModelAdmin

from infrastructure.oauth.google.models import GoogleAccount


@admin.register(GoogleAccount)
class GoogleAccountAdmin(UnfoldModelAdmin):
    """Accounts linked through Google.

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
        "hosted_domain",
        "last_login_at",
    )
    list_filter = ("email_verified",)
    search_fields = (
        "subject",
        "email",
        "user__username",
        "user__email",
        "hosted_domain",
    )
    autocomplete_fields = ("user",)
    exclude = ("access_token_encrypted", "refresh_token_encrypted")
    readonly_fields = ("id", "subject", "created_at", "updated_at", "last_login_at", "raw_claims")
    date_hierarchy = "created_at"
    ordering = ("-last_login_at",)
    list_select_related = ("user",)
