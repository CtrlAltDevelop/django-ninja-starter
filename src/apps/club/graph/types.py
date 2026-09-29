"""GraphQL types for clubs, ladders, missions and XP.

The same shapes the REST schemas publish, in Strawberry's vocabulary, built from
the same payload dictionaries the service returns -- so a field cannot exist on
one transport and go missing from the other by accident.

``criteria`` and ``metadata`` travel as the ``JSON`` scalar because their shape
belongs to whoever wrote the mission or integrated the event. A union of every
canonical shape would have to be edited the first time somebody stored something
new in one, and would make every generated client worse in the meantime.
"""

from datetime import datetime
from typing import Any

import strawberry
from strawberry.scalars import JSON


@strawberry.type
class LevelType:
    """One rung of a club's ladder."""

    id: str
    position: int
    """The rung's number, from 1 upwards with no gaps."""

    name: str
    xp_required: int
    """Total lifetime XP that reaches this rung. A threshold, not a cost."""

    logo: str
    perks: str
    metadata: JSON


@strawberry.type
class ClubBriefType:
    id: str
    name: str
    slug: str
    logo: str


@strawberry.type
class ClubType:
    """One club. ``levels`` is filled in when the club was asked for by name."""

    id: str
    name: str
    slug: str
    description: str
    logo: str
    status: str
    join_policy: str
    is_open: bool
    member_count: int
    metadata: JSON
    created_at: datetime
    levels: list[LevelType]


@strawberry.type
class MembershipType:
    """Where one account stands, which is the answer to most questions here."""

    id: str
    club: ClubBriefType
    status: str
    xp: int
    """Lifetime XP: the sum of every award, never a stored counter."""

    level: LevelType | None
    next_level: LevelType | None
    xp_to_next: int
    progress: float
    """How far between this rung and the next, 0.0 to 1.0. 1.0 at the top."""

    joined_at: datetime


@strawberry.type
class MissionType:
    """One mission, and how far the caller has got with it."""

    id: str
    code: str
    title: str
    description: str
    event: str
    criteria: JSON
    xp: int
    repeat: str
    target_count: int
    count: int
    completions: int
    completed: bool
    available: bool
    last_completed_at: datetime | None
    starts_at: datetime | None
    ends_at: datetime | None


@strawberry.type
class AwardType:
    """One payment of XP. Immutable, and the audit trail behind a level."""

    id: str
    xp: int
    reason: str
    reference: str
    mission_id: str | None
    metadata: JSON
    created_at: datetime


@strawberry.type
class RankType:
    """One row of a club's leaderboard."""

    position: int
    membership_id: str
    username: str
    xp: int
    level: LevelType | None
    is_you: bool


@strawberry.type
class EventType:
    """One thing this deployment can build a mission out of."""

    key: str
    label: str
    description: str
    value_label: str
    fields: JSON
    source: str


def _moment(value: Any) -> Any:
    """A timestamp the service rendered as text, back into one Strawberry can serialise."""
    return datetime.fromisoformat(value) if value else None


def level_type(row: dict[str, Any] | None) -> LevelType | None:
    if row is None:
        return None
    return LevelType(
        id=str(row["id"]),
        position=row["position"],
        name=row["name"],
        xp_required=row["xp_required"],
        logo=row["logo"],
        perks=row["perks"],
        metadata=row["metadata"],
    )


def club_type(row: dict[str, Any]) -> ClubType:
    return ClubType(
        id=str(row["id"]),
        name=row["name"],
        slug=row["slug"],
        description=row["description"],
        logo=row["logo"],
        status=row["status"],
        join_policy=row["join_policy"],
        is_open=row["is_open"],
        member_count=row["member_count"],
        metadata=row["metadata"],
        created_at=_moment(row["created_at"]),
        levels=[rung for level in row.get("levels", []) if (rung := level_type(level))],
    )


def membership_type(row: dict[str, Any]) -> MembershipType:
    return MembershipType(
        id=str(row["id"]),
        club=ClubBriefType(
            id=str(row["club"]["id"]),
            name=row["club"]["name"],
            slug=row["club"]["slug"],
            logo=row["club"]["logo"],
        ),
        status=row["status"],
        xp=row["xp"],
        level=level_type(row["level"]),
        next_level=level_type(row["next_level"]),
        xp_to_next=row["xp_to_next"],
        progress=row["progress"],
        joined_at=_moment(row["joined_at"]),
    )


def mission_type(row: dict[str, Any]) -> MissionType:
    return MissionType(
        id=str(row["id"]),
        code=row["code"],
        title=row["title"],
        description=row["description"],
        event=row["event"],
        criteria=row["criteria"],
        xp=row["xp"],
        repeat=row["repeat"],
        target_count=row["target_count"],
        count=row["count"],
        completions=row["completions"],
        completed=row["completed"],
        available=row["available"],
        last_completed_at=_moment(row["last_completed_at"]),
        starts_at=_moment(row["starts_at"]),
        ends_at=_moment(row["ends_at"]),
    )


def award_type(row: dict[str, Any]) -> AwardType:
    return AwardType(
        id=str(row["id"]),
        xp=row["xp"],
        reason=row["reason"],
        reference=row["reference"],
        mission_id=str(row["mission_id"]) if row["mission_id"] else None,
        metadata=row["metadata"],
        created_at=_moment(row["created_at"]),
    )


def rank_type(row: dict[str, Any]) -> RankType:
    return RankType(
        position=row["position"],
        membership_id=str(row["membership_id"]),
        username=row["username"],
        xp=row["xp"],
        level=level_type(row["level"]),
        is_you=row["is_you"],
    )
