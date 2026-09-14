"""One account, one club -- and the ways somebody would try to be in two.

The rule the whole app rests on. It is a database constraint rather than a
service check, so the test that matters is the one that goes around the service.
"""

from typing import Any

import pytest
from django.db import IntegrityError, transaction

from apps.club.errors import AlreadyAMember, ClubClosed, NotAMember
from apps.club.models import Club, JoinPolicy, Membership, MembershipStatus
from apps.club.services import club_service

pytestmark = pytest.mark.django_db


def test_joining_puts_an_account_on_the_first_rung(alice: Any, club: Club) -> None:
    standing = club_service.join(alice, club.slug)

    assert standing["club"]["slug"] == "explorers"
    assert standing["xp"] == 0
    assert standing["level"]["name"] == "Bronze"
    assert standing["next_level"]["name"] == "Silver"
    assert standing["xp_to_next"] == 100


def test_an_account_cannot_be_in_two_clubs(alice: Any, club: Club, other_club: Club) -> None:
    club_service.join(alice, club.slug)

    with pytest.raises(AlreadyAMember):
        club_service.join(alice, other_club.slug)

    assert Membership.objects.filter(user=alice).count() == 1


def test_the_database_refuses_a_second_membership_row(
    alice: Any, club: Club, other_club: Club
) -> None:
    """Going around the service entirely: the constraint is the real rule.

    Two requests racing to join two clubs both pass any check a service could
    make, so the one-to-one is what actually decides -- and this is the only test
    that proves it is there.
    """
    club_service.join(alice, club.slug)

    with pytest.raises(IntegrityError), transaction.atomic():
        Membership.objects.create(user=alice, club=other_club)


def test_joining_the_club_you_are_in_is_not_an_error(alice: Any, club: Club) -> None:
    """A double-tapped button is not a mistake worth a 409."""
    first = club_service.join(alice, club.slug)
    again = club_service.join(alice, club.slug)

    assert first["id"] == again["id"]


def test_leaving_keeps_the_history_and_lets_you_come_back(
    member: Any, club: Club, mission: Any, test_event: str
) -> None:
    """Leaving is not a way to reset a ladder you are near the top of."""
    from apps.club.services import track

    track(member, test_event)
    assert club_service.me(member)["xp"] == 50

    club_service.leave(member)
    with pytest.raises(NotAMember):
        club_service.me(member)

    club_service.join(member, club.slug)
    assert club_service.me(member)["xp"] == 50


def test_an_invite_only_club_cannot_be_joined_by_asking(alice: Any, club: Club) -> None:
    club.join_policy = str(JoinPolicy.INVITE)
    club.save()

    with pytest.raises(ClubClosed, match="by invitation"):
        club_service.join(alice, club.slug)

    club_service.add_member(alice, club.slug)
    assert club_service.me(alice)["club"]["slug"] == "explorers"


def test_a_closed_club_takes_nobody_by_any_route(alice: Any, club: Club) -> None:
    club.status = "closed"
    club.save()

    with pytest.raises(ClubClosed):
        club_service.join(alice, club.slug)
    with pytest.raises(ClubClosed):
        club_service.add_member(alice, club.slug)


def test_an_account_in_no_club_is_told_so_rather_than_given_an_empty_object(
    alice: Any,
) -> None:
    with pytest.raises(NotAMember):
        club_service.me(alice)


def test_a_member_who_left_is_not_a_member(member: Any) -> None:
    club_service.leave(member)

    row = Membership.objects.get(user=member)
    assert row.status == str(MembershipStatus.LEFT)
    assert row.left_at is not None
