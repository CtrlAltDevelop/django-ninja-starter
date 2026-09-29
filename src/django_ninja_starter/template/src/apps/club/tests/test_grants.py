"""XP paid by hand, and the record it has to leave.

The counterpart of an adjustment in the wallet: no event, no mission, somebody's
decision -- which is exactly why it needs a reason and an operator on it.
"""

from typing import Any

import pytest

from apps.club.errors import ClubError, NotAMember
from apps.club.models import XpAward
from apps.club.services import club_service

pytestmark = pytest.mark.django_db


def test_a_grant_pays_and_records_who_made_it(member: Any, operator: Any) -> None:
    award = club_service.grant(
        member, xp=75, reason="Sorry about the outage", reference="goodwill:1", by=operator
    )

    assert award["xp"] == 75
    # Recorded on the row for the back office, and never shown to the member.
    assert XpAward.objects.get(reference="goodwill:1").metadata["granted_by"] == str(operator.pk)
    assert "granted_by" not in award["metadata"]
    assert club_service.me(member)["xp"] == 75


def test_an_unexplained_grant_is_refused(member: Any) -> None:
    with pytest.raises(ClubError, match="needs a reason"):
        club_service.grant(member, xp=10, reason="   ", reference="silent")


def test_a_grant_without_a_reference_is_refused(member: Any) -> None:
    """The reference is what makes a retry safe, so there is no grant without one."""
    with pytest.raises(ClubError, match="reference is required"):
        club_service.grant(member, xp=10, reason="Because", reference="")


def test_the_same_grant_reference_pays_once(member: Any) -> None:
    first = club_service.grant(member, xp=10, reason="Once", reference="same")
    again = club_service.grant(member, xp=10, reason="Once", reference="same")

    assert first["id"] == again["id"]
    assert club_service.me(member)["xp"] == 10


def test_zero_or_less_is_not_a_grant(member: Any) -> None:
    with pytest.raises(ClubError, match="at least 1 XP"):
        club_service.grant(member, xp=0, reason="Nothing", reference="zero")


def test_granting_to_somebody_in_no_club_is_refused(alice: Any) -> None:
    with pytest.raises(NotAMember):
        club_service.grant(alice, xp=10, reason="Why", reference="nope")


def test_nothing_is_granted_into_an_archived_club(member: Any, club: Any) -> None:
    from apps.club.errors import ClubClosed

    club.status = "archived"
    club.save()

    with pytest.raises(ClubClosed):
        club_service.grant(member, xp=10, reason="r", reference="late")


def test_a_reused_reference_for_a_different_grant_is_refused(member: Any) -> None:
    """Replaying a reference is one grant; reusing it for another amount is not a success."""
    club_service.grant(member, xp=300, reason="Beta", reference="beta")

    with pytest.raises(ClubError, match="already used"):
        club_service.grant(member, xp=50, reason="Beta again", reference="beta")
    assert XpAward.objects.filter(reference="beta").count() == 1
