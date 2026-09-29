"""The shapes this app answers with, and the few it accepts.

Written out rather than generated from the models, for the reason every door
here is: a schema derived from a table publishes whatever the table happens to
hold, and the first private column added to a model becomes a field in the API
nobody decided to publish.
"""

from typing import Any
from uuid import UUID

from ninja import Schema


class LevelOut(Schema):
    """One rung of a club's ladder."""

    id: UUID
    position: int
    """The rung's number, from 1 upwards with no gaps."""

    name: str
    xp_required: int
    """Total lifetime XP that reaches this rung. A threshold, not a cost."""

    logo: str
    perks: str
    metadata: dict[str, Any]


class ClubBrief(Schema):
    """Just enough of a club to render it beside something else."""

    id: UUID
    name: str
    slug: str
    logo: str


class ClubOut(Schema):
    """One club. `levels` is present when the club was asked for by name."""

    id: UUID
    name: str
    slug: str
    description: str
    logo: str
    status: str
    join_policy: str
    is_open: bool
    """Whether an account can put itself in, as opposed to being put in."""

    member_count: int
    metadata: dict[str, Any]
    created_at: str
    levels: list[LevelOut] = []


class MembershipOut(Schema):
    """Where one account stands, which is the answer to most questions here."""

    id: UUID
    club: ClubBrief
    status: str
    xp: int
    """Lifetime XP: the sum of every award, never a stored counter."""

    level: LevelOut | None
    next_level: LevelOut | None
    xp_to_next: int
    progress: float
    """How far between this rung and the next, 0.0 to 1.0. 1.0 at the top."""

    joined_at: str


class MissionOut(Schema):
    """One mission, and how far the caller has got with it."""

    id: UUID
    code: str
    title: str
    description: str
    event: str
    """The registered event key this mission listens for."""

    criteria: dict[str, Any]
    xp: int
    repeat: str
    target_count: int
    count: int
    """Matching events seen since the current run began."""

    completions: int
    completed: bool
    available: bool
    """Whether it can be earned again right now."""

    last_completed_at: str | None
    starts_at: str | None
    ends_at: str | None


class AwardOut(Schema):
    """One payment of XP. Immutable, and the audit trail behind a level."""

    id: UUID
    xp: int
    reason: str
    reference: str
    mission_id: UUID | None
    metadata: dict[str, Any]
    created_at: str


class AwardPage(Schema):
    awards: list[AwardOut]
    count: int


class RankOut(Schema):
    """One row of a club's leaderboard."""

    position: int
    membership_id: UUID
    username: str
    xp: int
    level: LevelOut | None
    is_you: bool


class EventOut(Schema):
    """One thing this deployment can build a mission out of.

    Generated from what is actually registered, so it is the honest list rather
    than a documented one: an app that is not installed contributes nothing.
    """

    key: str
    label: str
    description: str
    value_label: str
    fields: dict[str, str]
    source: str


class JoinIn(Schema):
    slug: str
    """The club to join, by the name it carries in a URL."""
