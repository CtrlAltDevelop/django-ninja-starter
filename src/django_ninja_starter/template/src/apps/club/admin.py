"""The back office: defining clubs and ladders, writing missions, granting XP.

Two rules shape these screens.

**A ladder is edited as a ladder.** Levels are an inline on the club rather than
a changelist of their own, because the rules that make them a ladder -- numbered
from one, no gaps, thresholds increasing -- are about the set. The inline's
formset checks the whole set on save, so a rung saved individually cannot leave a
club with a gap at three that nobody notices until a member lands in it.

**Nothing that moves XP is typed into a row.** Awards are read-only: they are
what a member's level is derived from, so a hand-edited one is a level with no
explanation. XP granted by hand goes through the service, on the membership
screen, which writes an award with a reason and the operator's name on it.
"""

from typing import Any

from django import forms
from django.contrib import admin, messages
from django.core.exceptions import ValidationError
from django.db.models import QuerySet, Sum
from django.http import HttpRequest

from apps.club.errors import ClubError, InvalidLevels
from apps.club.events import choices as event_choices
from apps.club.models import (
    Club,
    ClubLevel,
    Membership,
    MembershipStatus,
    Mission,
    MissionProgress,
    XpAward,
)
from apps.club.services import _validated_ladder, club_service
from apps.club.standing import ladder, level_for
from apps.club.theme import (
    ChoicesDropdownFilter,
    ModelAdmin,
    TabularInline,
    display,
    dropdown_filter,
)


def _run(request: HttpRequest, call: Any, *args: Any, **kwargs: Any) -> bool:
    """Call a service method, reporting its refusal as a message rather than a 500.

    Every rule this app enforces is a :class:`ClubError`, so an operator who
    tries something the app will not do gets the same sentence a client would.
    """
    try:
        call(*args, **kwargs)
    except ClubError as refusal:
        messages.error(request, str(refusal))
        return False
    return True


class LevelInlineFormSet(forms.BaseInlineFormSet):
    """Checks the ladder as a whole, which is the only way these rules can be checked."""

    def clean(self) -> None:
        super().clean()
        if any(self.errors):
            return
        rungs = [
            {
                "position": form.cleaned_data["position"],
                "name": form.cleaned_data.get("name", ""),
                "xp_required": form.cleaned_data.get("xp_required", 0),
            }
            for form in self.forms
            if form.cleaned_data and not form.cleaned_data.get("DELETE")
        ]
        try:
            _validated_ladder(rungs)
        except InvalidLevels as refusal:
            raise ValidationError(str(refusal)) from None


class ClubLevelInline(TabularInline):
    model = ClubLevel
    formset = LevelInlineFormSet
    extra = 0
    fields = ("position", "name", "xp_required", "logo", "perks")
    ordering = ("position",)


@admin.register(Club)
class ClubAdmin(ModelAdmin):
    """A club and its ladder on one screen, because they are one decision."""

    list_display = ("name", "slug", "status", "join_policy", "levels_display", "members_display")
    list_filter = (
        dropdown_filter("status", ChoicesDropdownFilter),
        dropdown_filter("join_policy", ChoicesDropdownFilter),
    )
    search_fields = ("name", "slug", "description")
    prepopulated_fields = {"slug": ("name",)}
    inlines = (ClubLevelInline,)

    @display(description="Levels")
    def levels_display(self, instance: Club) -> str:
        rungs = instance.levels.count()
        return f"{rungs} rung{'s' if rungs != 1 else ''}" if rungs else "no ladder yet"

    @display(description="Members")
    def members_display(self, instance: Club) -> str:
        return str(instance.memberships.filter(status=str(MembershipStatus.ACTIVE)).count())


class MissionForm(forms.ModelForm):
    """The event is picked from what is registered, never typed.

    A free-text event field is a mission that silently never fires, discovered
    weeks later when nobody has earned anything. The dropdown is generated from
    the registry, so it lists exactly what this deployment emits.
    """

    class Meta:
        model = Mission
        fields = (
            "club",
            "code",
            "title",
            "description",
            "event",
            "criteria",
            "xp",
            "repeat",
            "target_count",
            "is_enabled",
            "starts_at",
            "ends_at",
            "metadata",
        )

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        available = event_choices()
        current = self.instance.event if self.instance and self.instance.pk else ""
        if current and current not in {key for key, _ in available}:
            # An event whose app has since been uninstalled: kept visible so the
            # mission can be read and fixed rather than silently losing its rule.
            available = [(current, f"{current} (no longer registered)"), *available]
        self.fields["event"] = forms.ChoiceField(
            choices=available,
            help_text="What this mission listens for. The list is what this deployment emits.",
        )

    def clean(self) -> dict[str, Any]:
        cleaned = super().clean() or {}
        if self.errors:
            return cleaned
        from apps.club.criteria import validate

        try:
            cleaned["criteria"] = validate(cleaned.get("criteria"))
        except ClubError as refusal:
            raise ValidationError({"criteria": str(refusal)}) from None
        return cleaned


@admin.register(Mission)
class MissionAdmin(ModelAdmin):
    """What a club pays for. Disabled by default, because a live mission pays out."""

    form = MissionForm
    list_display = ("title", "club", "event", "xp", "repeat", "target_count", "is_enabled")
    list_filter = (
        "is_enabled",
        dropdown_filter("repeat", ChoicesDropdownFilter),
        "club",
    )
    search_fields = ("title", "code", "event", "description")
    actions = ("enable_missions", "disable_missions")

    @admin.action(description="Enable the selected missions")
    def enable_missions(self, request: HttpRequest, queryset: QuerySet) -> None:
        messages.success(request, f"Enabled {queryset.update(is_enabled=True)}.")

    @admin.action(description="Disable the selected missions")
    def disable_missions(self, request: HttpRequest, queryset: QuerySet) -> None:
        messages.success(request, f"Disabled {queryset.update(is_enabled=False)}.")


class GrantForm(forms.Form):
    """What an operator fills in to pay XP by hand."""

    xp = forms.IntegerField(min_value=1)
    reason = forms.CharField(
        max_length=255, help_text="Required. The only record of why this level moved."
    )


class XpAwardInline(TabularInline):
    model = XpAward
    extra = 0
    fields = ("created_at", "xp", "reason", "mission", "reference")
    readonly_fields = fields
    ordering = ("-created_at",)

    def has_add_permission(self, request: HttpRequest, obj: Any = None) -> bool:
        """No. An award typed in by hand is a level with no explanation."""
        return False


@admin.register(Membership)
class MembershipAdmin(ModelAdmin):
    """Who is in which club, what they have earned, and the one way to pay by hand."""

    list_display = ("user", "club", "status", "xp_display", "level_display", "joined_at")
    list_filter = (dropdown_filter("status", ChoicesDropdownFilter), "club")
    search_fields = ("user__username", "user__email", "club__name")
    autocomplete_fields = ("user", "club")
    inlines = (XpAwardInline,)
    actions = ("grant_ten_xp",)

    def get_queryset(self, request: HttpRequest) -> QuerySet:
        return super().get_queryset(request).select_related("user", "club")

    def get_readonly_fields(self, request: HttpRequest, obj: Any = None) -> Any:
        """The XP is derived, so the screen shows it and cannot be used to set it."""
        return ("joined_at", "left_at", "updated_at") if obj else ()

    @display(description="XP")
    def xp_display(self, instance: Membership) -> str:
        total = instance.awards.aggregate(total=Sum("xp"))["total"] or 0
        return str(total)

    @display(description="Level")
    def level_display(self, instance: Membership) -> str:
        total = instance.awards.aggregate(total=Sum("xp"))["total"] or 0
        reached, _ = level_for(ladder(instance.club_id), int(total))
        return str(reached) if reached is not None else "—"

    @admin.action(description="Grant 10 XP to the selected members")
    def grant_ten_xp(self, request: HttpRequest, queryset: QuerySet) -> None:
        """A fixed, small grant, through the service so it is recorded like any other.

        Deliberately not "grant any amount from a changelist action": an action
        that took a number would be a way to mint a level from a list view with
        one click and no second thought. A larger correction is a decision, and a
        decision belongs on the member's own screen.
        """
        granted = sum(
            1
            for membership in queryset.filter(status=str(MembershipStatus.ACTIVE))
            if _run(
                request,
                club_service.grant,
                membership.user,
                xp=10,
                reason="Granted from the admin.",
                reference=f"grant:{membership.pk}:{XpAward.objects.filter(membership=membership).count()}",
                by=request.user,
            )
        )
        messages.success(request, f"Granted 10 XP to {granted}.")


@admin.register(XpAward)
class XpAwardAdmin(ModelAdmin):
    """The ledger a level is derived from. Read-only, in both directions."""

    list_display = ("created_at", "membership", "xp", "reason", "mission")
    list_filter = ("membership__club",)
    search_fields = ("reason", "reference", "membership__user__username")
    date_hierarchy = "created_at"

    def get_queryset(self, request: HttpRequest) -> QuerySet:
        return super().get_queryset(request).select_related("membership__user", "mission")

    def has_add_permission(self, request: HttpRequest) -> bool:
        return False

    def has_change_permission(self, request: HttpRequest, obj: Any = None) -> bool:
        return False

    def has_delete_permission(self, request: HttpRequest, obj: Any = None) -> bool:
        """No. Deleting an award silently lowers a level and explains nothing."""
        return False


@admin.register(MissionProgress)
class MissionProgressAdmin(ModelAdmin):
    """How far each member has got. Read-only: progress is a consequence, not an input."""

    list_display = ("membership", "mission", "count", "completions", "last_completed_at")
    list_filter = ("mission__club",)
    search_fields = ("membership__user__username", "mission__title")

    def get_queryset(self, request: HttpRequest) -> QuerySet:
        return super().get_queryset(request).select_related("membership__user", "mission")

    def has_add_permission(self, request: HttpRequest) -> bool:
        return False

    def has_change_permission(self, request: HttpRequest, obj: Any = None) -> bool:
        return False
