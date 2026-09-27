"""The back office: the ladder checked as a ladder, and XP that cannot be typed in.

What is worth testing here is not that the screens render but that they refuse:
a ladder with a gap saved through a formset, an award edited by hand, a mission
listening for an event nothing emits.
"""

from typing import Any

import pytest
from django.contrib.admin.sites import AdminSite, site
from django.contrib.auth import get_user_model
from django.contrib.messages.storage.fallback import FallbackStorage
from django.test import Client, RequestFactory
from django.urls import reverse

from apps.club.admin import MissionForm, MissionProgressAdmin, XpAwardAdmin
from apps.club.models import Club, Mission, MissionProgress, XpAward
from apps.club.services import club_service

pytestmark = pytest.mark.django_db


def admin_request(user: Any) -> Any:
    request = RequestFactory().post("/admin/")
    request.user = user
    request.session = {}
    request._messages = FallbackStorage(request)
    return request


@pytest.fixture
def superuser(db: None) -> Any:
    return get_user_model().objects.create_superuser(
        username="root", email="root@example.test", password="x"
    )


def test_an_award_cannot_be_added_edited_or_deleted(superuser: Any) -> None:
    """It is what a level is derived from, so a hand-edited one is an unexplained level."""
    screen = XpAwardAdmin(XpAward, AdminSite())
    request = admin_request(superuser)

    assert screen.has_add_permission(request) is False
    assert screen.has_change_permission(request) is False
    assert screen.has_delete_permission(request) is False


def test_progress_is_read_only(superuser: Any) -> None:
    """Progress is a consequence of events, not something an operator sets."""
    screen = MissionProgressAdmin(MissionProgress, AdminSite())
    request = admin_request(superuser)

    assert screen.has_add_permission(request) is False
    assert screen.has_change_permission(request) is False


def test_a_mission_cannot_listen_for_an_event_nothing_emits(club: Club, test_event: str) -> None:
    """A free-text event field is a mission that silently never fires."""
    form = MissionForm(
        data={
            "club": str(club.pk),
            "code": "typo",
            "title": "Typo",
            "event": "tests.thing.payed",
            "xp": 10,
            "repeat": "once",
            "target_count": 1,
            "criteria": "{}",
            "metadata": "{}",
        }
    )

    assert form.is_valid() is False
    assert "event" in form.errors


def test_a_mission_with_a_typo_in_its_criteria_is_refused(club: Club, test_event: str) -> None:
    form = MissionForm(
        data={
            "club": str(club.pk),
            "code": "bad",
            "title": "Bad",
            "event": test_event,
            "xp": 10,
            "repeat": "once",
            "target_count": 1,
            "criteria": '{"min_vlaue": 5}',
            "metadata": "{}",
        }
    )

    assert form.is_valid() is False
    assert "criteria" in form.errors


def test_a_good_mission_saves(club: Club, test_event: str) -> None:
    form = MissionForm(
        data={
            "club": str(club.pk),
            "code": "good",
            "title": "Good",
            "event": test_event,
            "xp": 10,
            "repeat": "once",
            "target_count": 1,
            "criteria": '{"min_value": 5}',
            "metadata": "{}",
        }
    )

    assert form.is_valid(), form.errors


def test_granting_from_the_changelist_goes_through_the_service(member: Any, superuser: Any) -> None:
    from apps.club.admin import MembershipAdmin
    from apps.club.models import Membership

    screen = MembershipAdmin(Membership, AdminSite())
    screen.grant_ten_xp(admin_request(superuser), Membership.objects.filter(user=member))

    assert club_service.me(member)["xp"] == 10


def test_every_changelist_renders_with_a_row_in_it(
    member: Any, mission: Mission, test_event: str, superuser: Any
) -> None:
    """The cheapest guard against a broken `list_display` reaching production."""
    from apps.club.services import track

    track(member, test_event, reference="one")
    client = Client()
    client.force_login(superuser)

    for model in site._registry:
        if model._meta.app_label != "club":
            continue
        url = reverse(f"admin:club_{model._meta.model_name}_changelist")
        assert client.get(url).status_code == 200, url


def test_the_membership_screen_links_to_the_grant_screen(member: Any, superuser: Any) -> None:
    from apps.club.models import Membership

    client = Client()
    client.force_login(superuser)
    membership = Membership.objects.get(user=member)

    page = client.get(reverse("admin:club_membership_change", args=(membership.pk,)))

    assert reverse("admin:club_membership_grant", args=(membership.pk,)) in page.content.decode()


def test_the_grant_screen_pays_once_however_often_it_is_submitted(
    member: Any, superuser: Any
) -> None:
    """The reference is issued with the page, so a double-submitted form is one grant."""
    from apps.club.models import Membership

    client = Client()
    client.force_login(superuser)
    membership = Membership.objects.get(user=member)
    url = reverse("admin:club_membership_grant", args=(membership.pk,))

    page = client.get(url)
    assert page.status_code == 200
    reference = page.context["form"].initial["reference"]
    for _ in range(2):
        answer = client.post(url, {"xp": 30, "reason": "Found a bug.", "reference": reference})
        assert answer.status_code == 302

    assert club_service.me(member)["xp"] == 30
    award = XpAward.objects.get(membership=membership)
    assert award.reason == "Found a bug."
    assert award.metadata["granted_by"] == str(superuser.pk)


def test_a_grant_without_a_reason_is_not_written(member: Any, superuser: Any) -> None:
    from apps.club.models import Membership

    client = Client()
    client.force_login(superuser)
    membership = Membership.objects.get(user=member)
    url = reverse("admin:club_membership_grant", args=(membership.pk,))

    answer = client.post(url, {"xp": 30, "reason": "", "reference": "grant:no-reason"})

    assert answer.status_code == 200
    assert club_service.me(member)["xp"] == 0


def test_the_grant_screen_is_refused_without_permission_to_change_members(
    member: Any, operator: Any
) -> None:
    from apps.club.models import Membership

    client = Client()
    client.force_login(operator)
    membership = Membership.objects.get(user=member)

    answer = client.get(reverse("admin:club_membership_grant", args=(membership.pk,)))

    assert answer.status_code == 403


def test_mission_progress_cannot_be_deleted_by_hand(db: None) -> None:
    """Deleting it resets the run count, and the next run's award then never pays."""
    from django.contrib.admin.sites import AdminSite

    from apps.club.admin import MissionProgressAdmin
    from apps.club.models import MissionProgress

    assert not MissionProgressAdmin(MissionProgress, AdminSite()).has_delete_permission(None)


def test_a_membership_cannot_be_added_to_an_archived_club(alice: Any, club: Any) -> None:
    from apps.club.admin import MembershipForm

    club.status = "archived"
    club.save()
    form = MembershipForm(data={"user": alice.pk, "club": club.pk, "status": "active"})

    assert not form.is_valid()
    assert "not taking members" in str(form.errors)


def test_a_member_who_left_is_not_brought_back_by_the_form(member: Any, club: Any) -> None:
    """Rejoining resets `joined_at` and announces the join; the form does neither."""
    from apps.club.admin import MembershipForm
    from apps.club.models import Membership
    from apps.club.services import club_service

    club_service.leave(member)
    row = Membership.objects.get(user=member)
    form = MembershipForm(data={"status": "active"}, instance=row)
    form.fields = {"status": form.fields["status"]}

    assert not form.is_valid()


def test_a_suspension_can_still_be_set_from_the_form(member: Any) -> None:
    from apps.club.admin import MembershipForm
    from apps.club.models import Membership

    form = MembershipForm(
        data={"status": "suspended"}, instance=Membership.objects.get(user=member)
    )
    form.fields = {"status": form.fields["status"]}

    assert form.is_valid(), form.errors


def _add_member(client: Client, user: Any, club: Club) -> Any:
    return client.post(
        reverse("admin:club_membership_add"),
        {
            "user": user.pk,
            "club": club.pk,
            "awards-TOTAL_FORMS": 0,
            "awards-INITIAL_FORMS": 0,
            "_save": "Save",
        },
    )


def test_the_add_form_goes_through_the_service_and_puts_back_a_member_who_left(
    alice: Any, club: Club, superuser: Any, django_capture_on_commit_callbacks: Any
) -> None:
    """Invite-only is joined from the back office, including by somebody who left it."""
    from apps.club import signals
    from apps.club.models import JoinPolicy, Membership, MembershipStatus

    club.join_policy = str(JoinPolicy.INVITE)
    club.save()
    heard: list[Any] = []

    def receiver(**kwargs: Any) -> None:
        heard.append(kwargs)

    signals.member_joined.connect(receiver)
    client = Client()
    client.force_login(superuser)
    try:
        with django_capture_on_commit_callbacks(execute=True):
            assert _add_member(client, alice, club).status_code == 302
            club_service.leave(alice)
            assert _add_member(client, alice, club).status_code == 302
    finally:
        signals.member_joined.disconnect(receiver)

    row = Membership.objects.get(user=alice)
    assert row.status == str(MembershipStatus.ACTIVE)
    assert row.left_at is None
    assert len(heard) == 2


def test_the_form_refuses_marking_a_member_left(member: Any) -> None:
    """Leaving stamps `left_at` and announces it; a status dropdown does neither."""
    from apps.club.admin import MembershipForm
    from apps.club.models import Membership

    form = MembershipForm(data={"status": "left"}, instance=Membership.objects.get(user=member))
    form.fields = {"status": form.fields["status"]}

    assert not form.is_valid()
    assert "club service" in str(form.errors)


def test_the_admin_marks_both_ledgers_read_only_for_the_docs() -> None:
    assert MissionProgressAdmin.read_only_admin is True
    assert XpAwardAdmin.read_only_admin is True


def test_the_ladder_inline_accepts_a_whole_ladder_edit(club: Club) -> None:
    """0/100/300/600 to 0/350/400/600: each rung checked alone against the old rows fails."""
    from django.forms import inlineformset_factory

    from apps.club.admin import LevelInlineFormSet
    from apps.club.models import ClubLevel
    from apps.club.standing import ladder

    Formset = inlineformset_factory(
        Club,
        ClubLevel,
        formset=LevelInlineFormSet,
        fields=("position", "name", "xp_required"),
        extra=0,
    )
    rows = list(ladder(club.pk))
    data: dict[str, Any] = {"levels-TOTAL_FORMS": 4, "levels-INITIAL_FORMS": 4}
    for index, (row, xp) in enumerate(zip(rows, [0, 350, 400, 600], strict=True)):
        data |= {
            f"levels-{index}-id": row.pk,
            f"levels-{index}-club": club.pk,
            f"levels-{index}-position": row.position,
            f"levels-{index}-name": row.name,
            f"levels-{index}-xp_required": xp,
        }
    formset = Formset(data, instance=club)

    assert formset.is_valid(), formset.errors
    formset.save()
    assert [level.xp_required for level in ladder(club.pk)] == [0, 350, 400, 600]
    assert [level.pk for level in ladder(club.pk)] == [row.pk for row in rows]


def test_the_add_form_refuses_somebody_already_in_another_club(
    member: Any, other_club: Any
) -> None:
    from apps.club.admin import MembershipForm

    form = MembershipForm(data={"user": member.pk, "club": other_club.pk})

    assert not form.is_valid()
    assert "leave it first" in str(form.errors)


def test_the_form_refuses_activating_a_member_of_an_archived_club(member: Any, club: Any) -> None:
    from apps.club.admin import MembershipForm
    from apps.club.models import Membership

    row = Membership.objects.get(user=member)
    row.status = "suspended"
    row.save()
    club.status = "archived"
    club.save()
    form = MembershipForm(data={"status": "active"}, instance=row)
    form.fields = {"status": form.fields["status"]}

    assert not form.is_valid()
    assert "archived" in str(form.errors)
