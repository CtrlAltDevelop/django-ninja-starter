"""Where a member stands: their XP, the rung it puts them on, and the next one.

XP is derived rather than stored, so this is the only place that decides what a
member has -- and everything else, the API, the admin, the leaderboard, asks
here. A second implementation of "what level is this?" is how two screens end up
disagreeing about somebody's badge.

The level for an amount of XP is the highest rung whose threshold it has reached.
Because the ladder is contiguous and its thresholds strictly increase, that is a
single comparison rather than a search, and a member with more XP than the top
rung sits on the top rung rather than falling off the end.
"""

from dataclasses import dataclass
from typing import Any

from django.db.models import Sum

from apps.club.models import ClubLevel, Membership, XpAward


@dataclass(frozen=True)
class Standing:
    """One member's position, as every transport reports it."""

    xp: int
    level: ClubLevel | None
    next_level: ClubLevel | None

    @property
    def xp_to_next(self) -> int:
        """How much more is needed for the next rung. Zero at the top."""
        if self.next_level is None:
            return 0
        return max(0, self.next_level.xp_required - self.xp)

    @property
    def progress(self) -> float:
        """How far between this rung and the next, 0.0 to 1.0.

        At the top rung it is 1.0 rather than 0.0: a member who has finished the
        ladder is complete, not at the beginning of nothing.
        """
        if self.next_level is None:
            return 1.0
        floor = self.level.xp_required if self.level is not None else 0
        span = self.next_level.xp_required - floor
        if span <= 0:
            return 1.0
        return min(1.0, max(0.0, (self.xp - floor) / span))


def xp_of(membership: Membership) -> int:
    """The sum of what this member has been awarded. The number, not a cached copy."""
    total = XpAward.objects.filter(membership=membership).aggregate(total=Sum("xp"))["total"]
    return int(total or 0)


def level_for(levels: list[ClubLevel], xp: int) -> tuple[ClubLevel | None, ClubLevel | None]:
    """The rung this much XP reaches, and the one after it.

    ``levels`` is the club's ladder in order. Passed in rather than queried so a
    caller ranking a whole leaderboard reads the ladder once instead of once per
    member.
    """
    reached: ClubLevel | None = None
    following: ClubLevel | None = None
    for level in levels:
        if xp >= level.xp_required:
            reached = level
        else:
            following = level
            break
    return reached, following


def ladder(club_id: Any) -> list[ClubLevel]:
    """One club's rungs, in order."""
    return list(ClubLevel.objects.filter(club_id=club_id).order_by("position"))


def standing_of(membership: Membership, *, levels: list[ClubLevel] | None = None) -> Standing:
    """Everything about where one member stands, in one object."""
    rungs = ladder(membership.club_id) if levels is None else levels
    xp = xp_of(membership)
    reached, following = level_for(rungs, xp)
    return Standing(xp=xp, level=reached, next_level=following)
