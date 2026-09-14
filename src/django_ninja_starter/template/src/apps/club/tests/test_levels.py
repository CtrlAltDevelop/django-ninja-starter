"""The ladder: that it is one, and that XP lands a member on the right rung.

A ladder with a gap is not a stricter ladder, it is an ambiguous one, and the
ambiguity shows up as members sitting at a level the club never meant to exist.
Both the whole-ladder service and the one-row admin path are guarded, so both
are tested.
"""

from typing import Any

import pytest
from django.core.exceptions import ValidationError

from apps.club.errors import InvalidLevels
from apps.club.models import Club, ClubLevel, Mission, Repeat
from apps.club.services import club_service, track
from apps.club.standing import ladder, level_for

pytestmark = pytest.mark.django_db


def rungs(*pairs: tuple[int, str, int]) -> list[dict[str, Any]]:
    return [{"position": position, "name": name, "xp_required": xp} for position, name, xp in pairs]


def test_a_ladder_has_to_start_at_zero(club: Club) -> None:
    with pytest.raises(InvalidLevels, match="everybody starts"):
        club_service.set_levels(club.slug, rungs((1, "Bronze", 10)))


def test_a_ladder_cannot_have_a_gap(club: Club) -> None:
    with pytest.raises(InvalidLevels, match="no gaps"):
        club_service.set_levels(club.slug, rungs((1, "Bronze", 0), (3, "Gold", 100)))


def test_a_ladder_cannot_go_backwards(club: Club) -> None:
    with pytest.raises(InvalidLevels, match="more XP than the one below"):
        club_service.set_levels(
            club.slug, rungs((1, "Bronze", 0), (2, "Silver", 100), (3, "Gold", 50))
        )


def test_two_rungs_cannot_need_the_same_xp(club: Club) -> None:
    with pytest.raises(InvalidLevels):
        club_service.set_levels(club.slug, rungs((1, "Bronze", 0), (2, "Silver", 0)))


def test_every_rung_needs_a_name(club: Club) -> None:
    with pytest.raises(InvalidLevels, match="needs a name"):
        club_service.set_levels(club.slug, rungs((1, "", 0)))


def test_a_good_ladder_replaces_the_old_one(club: Club) -> None:
    written = club_service.set_levels(
        club.slug, rungs((1, "Wood", 0), (2, "Stone", 50), (3, "Iron", 200))
    )

    assert [rung["name"] for rung in written] == ["Wood", "Stone", "Iron"]
    assert [level.position for level in ladder(club.pk)] == [1, 2, 3]


def test_the_admin_path_refuses_a_rung_that_breaks_the_order(club: Club) -> None:
    """One row at a time, which is how the admin saves, and the same rule applies."""
    level = ClubLevel(club=club, position=3, name="Wrong", xp_required=50)

    with pytest.raises(ValidationError):
        level.clean()


def test_the_first_rung_cannot_cost_xp_through_the_admin_either(club: Club) -> None:
    level = ClubLevel(club=club, position=1, name="Start", xp_required=5)

    with pytest.raises(ValidationError, match="everybody starts"):
        level.clean()


@pytest.mark.parametrize(
    ("xp", "expected", "following"),
    [
        (0, "Bronze", "Silver"),
        (99, "Bronze", "Silver"),
        (100, "Silver", "Gold"),
        (299, "Silver", "Gold"),
        (300, "Gold", "Platinum"),
        (600, "Platinum", None),
        (10_000, "Platinum", None),
    ],
)
def test_xp_lands_on_the_right_rung(
    club: Club, xp: int, expected: str, following: str | None
) -> None:
    reached, next_up = level_for(ladder(club.pk), xp)

    assert reached is not None and reached.name == expected
    assert (next_up.name if next_up else None) == following


def test_earning_enough_moves_a_member_up(member: Any, club: Club, test_event: str) -> None:
    Mission.objects.create(
        club=club,
        code="grind",
        title="Grind",
        event=test_event,
        xp=40,
        repeat=str(Repeat.EVERY_TIME),
        is_enabled=True,
    )

    for index in range(2):
        track(member, test_event, reference=f"a{index}")
    assert club_service.me(member)["level"]["name"] == "Bronze"

    track(member, test_event, reference="a2")
    standing = club_service.me(member)
    assert standing["xp"] == 120
    assert standing["level"]["name"] == "Silver"
    assert standing["xp_to_next"] == 180


def test_a_member_at_the_top_is_complete_rather_than_at_the_start_of_nothing(
    member: Any,
) -> None:
    club_service.grant(member, xp=600, reason="Everything", reference="all")

    standing = club_service.me(member)
    assert standing["level"]["name"] == "Platinum"
    assert standing["next_level"] is None
    assert standing["xp_to_next"] == 0
    assert standing["progress"] == 1.0
