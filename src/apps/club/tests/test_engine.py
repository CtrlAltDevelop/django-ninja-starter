"""Missions completing themselves, which is the thing this app is for.

Nobody claims a mission here. Another app says "this happened", and every
assertion below is about what the engine decided on its own: which missions
matched, how far they got, what they paid, and -- the one that matters most --
what happens when the same thing is reported twice.
"""

from typing import Any

import pytest
from django.utils import timezone

from apps.club.errors import UnknownEvent
from apps.club.models import Club, Mission, MissionProgress, Repeat, XpAward
from apps.club.services import club_service, track

pytestmark = pytest.mark.django_db


def test_an_event_completes_a_mission_and_pays_without_anybody_claiming_it(
    member: Any, mission: Mission, test_event: str
) -> None:
    paid = track(member, test_event)

    assert [award["xp"] for award in paid] == [50]
    assert club_service.me(member)["xp"] == 50


def test_the_same_event_reported_twice_pays_once(
    member: Any, mission: Mission, test_event: str
) -> None:
    """A redelivered webhook, a retried request, a job run twice: all one award."""
    track(member, test_event, reference="thing:1")
    track(member, test_event, reference="thing:1")

    assert club_service.me(member)["xp"] == 50
    assert XpAward.objects.filter(membership__user=member).count() == 1


def test_a_once_mission_pays_once_however_often_it_happens(
    member: Any, mission: Mission, test_event: str
) -> None:
    for index in range(5):
        track(member, test_event, reference=f"thing:{index}")

    assert club_service.me(member)["xp"] == 50


def test_an_every_time_mission_pays_every_time(
    member: Any, mission: Mission, test_event: str
) -> None:
    mission.repeat = str(Repeat.EVERY_TIME)
    mission.save()

    for index in range(3):
        track(member, test_event, reference=f"thing:{index}")

    assert club_service.me(member)["xp"] == 150


def test_a_mission_needing_three_events_pays_on_the_third(
    member: Any, mission: Mission, test_event: str
) -> None:
    mission.target_count = 3
    mission.save()

    track(member, test_event, reference="a")
    track(member, test_event, reference="b")
    assert club_service.me(member)["xp"] == 0

    track(member, test_event, reference="c")
    assert club_service.me(member)["xp"] == 50


def test_criteria_decide_which_events_count(member: Any, mission: Mission, test_event: str) -> None:
    """A mission that only counts the big ones ignores the small ones entirely."""
    mission.criteria = {"min_value": 100}
    mission.save()

    track(member, test_event, value=50, reference="small")
    assert club_service.me(member)["xp"] == 0

    track(member, test_event, value=150, reference="big")
    assert club_service.me(member)["xp"] == 50


def test_criteria_can_match_on_metadata(member: Any, mission: Mission, test_event: str) -> None:
    mission.criteria = {"equals": {"kind": "gold"}}
    mission.save()

    track(member, test_event, metadata={"kind": "tin"}, reference="one")
    assert club_service.me(member)["xp"] == 0

    track(member, test_event, metadata={"kind": "gold"}, reference="two")
    assert club_service.me(member)["xp"] == 50


def test_a_disabled_mission_pays_nothing(member: Any, mission: Mission, test_event: str) -> None:
    mission.is_enabled = False
    mission.save()

    assert track(member, test_event) == []
    assert club_service.me(member)["xp"] == 0


def test_a_mission_outside_its_window_pays_nothing(
    member: Any, mission: Mission, test_event: str
) -> None:
    mission.starts_at = timezone.now() + timezone.timedelta(days=1)
    mission.save()

    assert track(member, test_event) == []


def test_tracking_for_somebody_in_no_club_does_nothing(alice: Any, test_event: str) -> None:
    """The caller is a shop, and a shop should not have to know who is in a club."""
    assert track(alice, test_event) == []


def test_tracking_an_event_nobody_registered_is_a_programming_mistake(member: Any) -> None:
    """Not silence: a typo'd key at a call site would otherwise be undetectable."""
    with pytest.raises(UnknownEvent):
        track(member, "tests.thing.payed")


def test_one_event_can_complete_several_missions(
    member: Any, club: Club, mission: Mission, test_event: str
) -> None:
    Mission.objects.create(
        club=club,
        code="second",
        title="Also this",
        event=test_event,
        xp=25,
        is_enabled=True,
    )

    paid = track(member, test_event, reference="once")

    assert sorted(award["xp"] for award in paid) == [25, 50]
    assert club_service.me(member)["xp"] == 75


def test_a_members_progress_is_theirs_alone(
    member: Any, bob: Any, club: Club, mission: Mission, test_event: str
) -> None:
    club_service.add_member(bob, club.slug)
    mission.target_count = 2
    mission.save()

    track(member, test_event, reference="a")
    track(bob, test_event, reference="a")

    assert club_service.me(member)["xp"] == 0
    assert club_service.me(bob)["xp"] == 0
    assert MissionProgress.objects.filter(count=1).count() == 2


def test_a_mission_for_another_club_is_not_earned(
    member: Any, other_club: Club, test_event: str
) -> None:
    Mission.objects.create(
        club=other_club,
        code="theirs",
        title="Their mission",
        event=test_event,
        xp=500,
        is_enabled=True,
    )

    assert track(member, test_event) == []
    assert club_service.me(member)["xp"] == 0


def test_an_archived_club_pays_nothing(
    member: Any, club: Club, mission: Mission, test_event: str
) -> None:
    """Its members are still `active`, but the club itself has stopped."""
    Club.objects.filter(pk=club.pk).update(status="archived")

    assert track(member, test_event) == []
    assert not XpAward.objects.exists()


def test_a_broken_signal_receiver_does_not_fail_the_join(
    alice: Any, club: Club, django_capture_on_commit_callbacks: Any
) -> None:
    """Committed is committed: a receiver's mail error must not turn it into a 500."""
    from apps.club import signals

    def broken(**kwargs: Any) -> None:
        raise RuntimeError("the mail server is down")

    signals.member_joined.connect(broken)
    try:
        with django_capture_on_commit_callbacks(execute=True):
            club_service.join(alice, club.slug)
    finally:
        signals.member_joined.disconnect(broken)

    assert club_service.me(alice)["club"]["slug"] == club.slug


def test_the_leaderboard_is_ranked_and_cut_by_the_database(
    member: Any, bob: Any, club: Club
) -> None:
    club_service.join(bob, club.slug)
    club_service.grant(bob, xp=10, reason="r", reference="b")

    board = club_service.leaderboard(member, limit=1)

    assert [row["username"] for row in board] == ["Member"]


def test_rejoining_starts_the_membership_again(member: Any, club: Club, other_club: Club) -> None:
    from apps.club.models import Membership

    first = Membership.objects.get(user=member).joined_at
    club_service.leave(member)
    club_service.join(member, other_club.slug)

    assert Membership.objects.get(user=member).joined_at > first


def test_the_award_count_is_every_award_not_the_page(member: Any) -> None:
    for number in range(3):
        club_service.grant(member, xp=1, reason="r", reference=f"g{number}")

    assert len(club_service.awards(member, limit=1)) == 1
    assert club_service.award_count(member) == 3


def test_a_long_reference_is_hashed_rather_than_overflowing(
    member: Any, mission: Mission, test_event: str
) -> None:
    long = "x" * 300

    assert len(track(member, test_event, reference=long)) == 1
    assert track(member, test_event, reference=long) == []
    assert all(len(award.reference) <= 200 for award in XpAward.objects.all())
