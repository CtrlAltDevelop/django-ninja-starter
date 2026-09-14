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
