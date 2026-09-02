"""Admin for accounts and their profiles.

``search_fields`` here is load-bearing: every other admin in the project reaches
an account through ``autocomplete_fields = ("user",)``, and Django refuses that
unless the target admin declares what it can be searched by.
"""

from typing import Any

from django.contrib import admin
from django.contrib.auth.admin import GroupAdmin as DjangoGroupAdmin
from django.contrib.auth.admin import UserAdmin as DjangoUserAdmin
from django.contrib.auth.models import Group
from unfold.admin import ModelAdmin, StackedInline
from unfold.contrib.filters.admin import BooleanRadioFilter, RelatedDropdownFilter
from unfold.decorators import display
from unfold.forms import AdminPasswordChangeForm, UserChangeForm, UserCreationForm

from infrastructure.accounts.models import Profile, User


class ProfileInline(StackedInline):
    """The profile, shown where somebody looking for it would look: on the account."""

    model = Profile
    can_delete = False
    verbose_name_plural = "Profile"
    readonly_fields = ("created_at", "updated_at")


@admin.register(User)
class UserAdmin(DjangoUserAdmin, ModelAdmin):
    """Django's own user admin, pointed at this project's model.

    Inherited rather than rewritten so the parts that are genuinely hard stay
    correct: the password field renders as a hashed-value widget with a change
    link instead of a text input, and setting a password goes through the
    dedicated form rather than saving whatever was typed.
    """

    add_form = UserCreationForm
    form = UserChangeForm
    change_password_form = AdminPasswordChangeForm
    inlines = (ProfileInline,)

    list_display = ("account", "status", "email_verified", "role", "date_joined")
    list_filter = (
        ("is_active", BooleanRadioFilter),
        ("is_staff", BooleanRadioFilter),
        ("is_superuser", BooleanRadioFilter),
        ("groups", RelatedDropdownFilter),
    )
    list_filter_submit = True
    warn_unsaved_form = True
    search_fields = ("username", "email", "profile__display_name")
    ordering = ("-date_joined",)
    readonly_fields = ("id", "date_joined", "updated_at", "last_login", "email_verified_at")
    filter_horizontal = ("groups", "user_permissions")
    date_hierarchy = "date_joined"

    fieldsets = (
        (None, {"fields": ("id", "username", "password")}),
        ("Contact", {"fields": ("email", "email_verified_at")}),
        (
            "Permissions",
            {
                "fields": (
                    "is_active",
                    "is_staff",
                    "is_superuser",
                    "groups",
                    "user_permissions",
                )
            },
        ),
        ("Dates", {"fields": ("last_login", "date_joined", "updated_at")}),
    )
    add_fieldsets = (
        (
            None,
            {
                "classes": ("wide",),
                "fields": ("username", "email", "usable_password", "password1", "password2"),
            },
        ),
    )

    @display(description="Account", header=True)
    def account(self, obj: User) -> list[str]:
        """Two lines: who they are, and how they are reached."""
        return [obj.username, obj.email or "no email"]

    @display(
        description="Status",
        label={"Active": "success", "Disabled": "danger"},
        ordering="is_active",
    )
    def status(self, obj: User) -> str:
        return "Active" if obj.is_active else "Disabled"

    @display(description="Role", label={"Superuser": "primary", "Staff": "info", "User": "info"})
    def role(self, obj: User) -> str:
        if obj.is_superuser:
            return "Superuser"
        return "Staff" if obj.is_staff else "User"

    @display(boolean=True, description="Email verified", ordering="email_verified_at")
    def email_verified(self, obj: User) -> bool:
        return obj.is_email_verified

    def get_queryset(self, request: Any) -> Any:
        """The list page reads the profile for its search, so fetch it alongside."""
        return super().get_queryset(request).select_related("profile")


@admin.register(Profile)
class ProfileAdmin(ModelAdmin):
    """Reachable on its own as well as inline, for searching by display name."""

    list_display = ("user", "display_name", "locale", "timezone", "marketing_opt_in", "updated_at")
    list_filter = ("marketing_opt_in", "locale", "timezone")
    search_fields = ("display_name", "user__username", "user__email")
    autocomplete_fields = ("user",)
    readonly_fields = ("created_at", "updated_at")
    ordering = ("-updated_at",)
    list_select_related = ("user",)


# Django registers Group with a plain ModelAdmin at import time. Left alone it
# is the one page in the whole admin that renders unstyled, which reads as a
# bug in the theme rather than as an app nobody re-registered.
admin.site.unregister(Group)


@admin.register(Group)
class GroupAdmin(DjangoGroupAdmin, ModelAdmin):
    """Django's group admin, themed like everything around it."""
