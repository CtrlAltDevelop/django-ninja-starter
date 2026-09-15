"""The ways the engine could be talked into paying for something that did not happen.

Each test is a hole that was open once: one event delivered three times finishing
a three-event mission, XP following a member into a club that never paid it, a
suspension lifted by leaving, and events the shop and the sign-in trail send that
nothing was listening for.
"""

from decimal import Decimal
from typing import Any

import pytest
from django.apps import apps as django_apps
from django.test import RequestFactory
from django.utils import timezone

from apps.club.errors import MembershipSuspended, MissionRefused
from apps.club.events import Occurrence
from apps.club.models import (
    Club,
    Membership,
    MembershipStatus,
    Mission,
    MissionProgress,
    Repeat,
    XpAward,
)
from apps.club.services import club_service, track

pytestmark = pytest.mark.django_db


def _mission(club: Club, event: str, **fields: Any) -> Mission:
    defaults: dict[str, Any] = {"title": fields.get("code", "m"), "xp": 10, "is_enabled": True}
    return Mission.objects.create(club=club, event=event, **{**defaults, **fields})


def test_one_event_delivered_three_times_does_not_finish_a_three_event_mission(
    member: Any, mission: Mission, test_event: str
) -> None:
    """The award's reference only covers the event that finishes a run."""
    mission.target_count = 3
    mission.save()

    for _ in range(3):
        track(member, test_event, reference="the-same-one")

    assert MissionProgress.objects.get(mission=mission).count == 1
    assert club_service.me(member)["xp"] == 0


def test_a_replayed_event_is_not_another_completion(
    member: Any, mission: Mission, test_event: str
) -> None:
    mission.repeat = str(Repeat.EVERY_TIME)
    mission.save()

    track(member, test_event, reference="one")
    track(member, test_event, reference="one")

    assert MissionProgress.objects.get(mission=mission).completions == 1
    assert club_service.me(member)["xp"] == 50


def test_xp_does_not_follow_a_member_into_another_club(
    member: Any, club: Club, other_club: Club, mission: Mission, test_event: str
) -> None:
    """Otherwise joining a new club is a way to arrive at the top of its ladder."""
    track(member, test_event, reference="one")
    club_service.leave(member)

    moved = club_service.join(member, other_club.slug)
    assert moved["xp"] == 0
    assert club_service.awards(member) == []
    assert club_service.leaderboard(member)[0]["xp"] == 0

    club_service.leave(member)
    assert club_service.join(member, club.slug)["xp"] == 50


def test_a_suspended_member_cannot_leave_their_way_out_of_it(member: Any, club: Club) -> None:
    Membership.objects.filter(user=member).update(status=str(MembershipStatus.SUSPENDED))

    with pytest.raises(MembershipSuspended):
        club_service.leave(member)
    assert club_service.join(member, club.slug)["status"] == "suspended"


def test_an_event_that_arrives_after_the_member_left_pays_nothing(
    member: Any, mission: Mission, test_event: str
) -> None:
    """The membership is read before the lock; what it is under the lock decides."""
    membership = club_service.membership_for(member)
    assert membership is not None
    club_service.leave(member)

    occurrence = Occurrence(key=test_event, user=member, reference="late")
    assert club_service._advance(membership, mission, occurrence, now=timezone.now()) is None
    assert not XpAward.objects.exists()


def test_joining_pays_a_welcome_mission_once_however_often_you_come_back(
    alice: Any, club: Club
) -> None:
    _mission(club, "club.member.joined", code="welcome", xp=20, repeat=str(Repeat.EVERY_TIME))

    assert club_service.join(alice, club.slug)["xp"] == 20
    club_service.leave(alice)
    assert club_service.join(alice, club.slug)["xp"] == 20


def test_a_negative_offset_is_the_first_page_rather_than_a_crash(member: Any) -> None:
    assert club_service.awards(member, offset=-5) == []
    assert len(club_service.clubs(offset=-1)) == 1


def test_a_mission_cannot_end_before_it_starts(club: Club, test_event: str) -> None:
    now = timezone.now()

    with pytest.raises(MissionRefused, match="end before it starts"):
        club_service.define_mission(
            club.slug,
            code="backwards",
            title="Backwards",
            event=test_event,
            xp=1,
            starts_at=now,
            ends_at=now - timezone.timedelta(hours=1),
        )


def test_a_sign_in_pays_a_sign_in_mission(
    member: Any, club: Club, django_capture_on_commit_callbacks: Any
) -> None:
    """Heard from the audit row every login method writes, not from each method."""
    from infrastructure.auth.core.flows import record_event
    from infrastructure.auth.core.models import AuthEventType

    _mission(club, "accounts.user.signed_in", code="back", xp=5, repeat=str(Repeat.DAILY))

    with django_capture_on_commit_callbacks(execute=True):
        for _ in range(2):
            record_event(
                RequestFactory().post("/"),
                AuthEventType.LOGIN_SUCCEEDED,
                user=member,
                method="password",
            )

    assert club_service.me(member)["xp"] == 5


@pytest.mark.skipif(not django_apps.is_installed("apps.shop"), reason="the shop is not installed")
def test_a_paid_order_pays_a_shop_mission_once(member: Any, club: Club) -> None:
    from apps.shop import signals as shop_signals

    _mission(
        club,
        "shop.order.paid",
        code="big-spender",
        xp=40,
        repeat=str(Repeat.EVERY_TIME),
        criteria={"min_value": 100},
    )
    order = {
        "id": "order-1",
        "number": "S1",
        "user_id": member.pk,
        "total": Decimal("150.00"),
        "currency": "USD",
        "items": 2,
    }

    shop_signals.order_paid.send(sender="apps.shop", order=order)
    shop_signals.order_paid.send(sender="apps.shop", order=order)

    assert club_service.me(member)["xp"] == 40


@pytest.mark.skipif(not django_apps.is_installed("apps.shop"), reason="the shop is not installed")
def test_a_published_review_pays_a_review_mission(member: Any, club: Club) -> None:
    from apps.shop import signals as shop_signals

    _mission(club, "shop.review.published", code="critic", xp=15, repeat=str(Repeat.EVERY_TIME))
    review = {"id": "review-1", "user_id": member.pk, "product_id": "p", "rating": 4}

    shop_signals.review_published.send(sender="apps.shop", review=review)
    shop_signals.review_published.send(sender="apps.shop", review=review)

    assert club_service.me(member)["xp"] == 15


def test_a_new_account_is_put_in_the_signup_club_and_paid_for_arriving(
    club: Club, settings: Any, django_capture_on_commit_callbacks: Any
) -> None:
    """Without a club to be put in, a mission on this event has nobody to pay."""
    from django.contrib.auth import get_user_model

    settings.CLUB_JOIN_ON_SIGNUP = club.slug
    _mission(club, "accounts.user.registered", code="arrived", xp=15)

    with django_capture_on_commit_callbacks(execute=True):
        newcomer = get_user_model().objects.create_user(username="newcomer", email="n@x.test")

    standing = club_service.me(newcomer)
    assert standing["club"]["slug"] == club.slug
    assert standing["xp"] == 15


def test_without_a_signup_club_a_new_account_is_in_none(
    club: Club, django_capture_on_commit_callbacks: Any
) -> None:
    from django.contrib.auth import get_user_model

    with django_capture_on_commit_callbacks(execute=True):
        newcomer = get_user_model().objects.create_user(username="newcomer", email="n@x.test")

    assert club_service.membership_for(newcomer, required=False) is None


def test_a_signup_club_that_does_not_exist_never_fails_the_sign_up(
    db: None, settings: Any, django_capture_on_commit_callbacks: Any
) -> None:
    from django.contrib.auth import get_user_model

    settings.CLUB_JOIN_ON_SIGNUP = "nowhere"

    with django_capture_on_commit_callbacks(execute=True):
        newcomer = get_user_model().objects.create_user(username="newcomer", email="n@x.test")

    assert get_user_model().objects.filter(pk=newcomer.pk).exists()
    assert club_service.membership_for(newcomer, required=False) is None
