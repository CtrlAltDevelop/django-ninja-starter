"""The club's contribution to the project's GraphQL schema.

The same division as the routes, and the same rule underneath it: every field
resolves the membership from the credential rather than from an argument, so
there is no identifier a caller could change to read somebody else's standing or
somebody else's club's members.

Every field is prefixed ``club`` because the project merges each installed app's
``Query`` into one root type, and a bare ``missions`` would collide with the next
app that has some. The merge refuses collisions rather than silently losing a
field, so the prefix is what keeps it quiet.

There is no mutation for completing a mission, here or anywhere: the engine is
told by the app where the thing actually happened. What a client may do is join,
and leave.
"""

from typing import Any, cast

import strawberry
from strawberry.scalars import JSON
from strawberry.types import Info

from apps.club.errors import ClubError
from apps.club.events import sorted_specs
from apps.club.graph.types import (
    AwardType,
    ClubType,
    EventType,
    LevelType,
    MembershipType,
    MissionType,
    RankType,
    award_type,
    club_type,
    level_type,
    membership_type,
    mission_type,
    rank_type,
)
from apps.club.services import club_service
from infrastructure.common.errors import ApiError
from infrastructure.common.graph.errors import require_caller, resolver
from infrastructure.common.identity import caller
from infrastructure.common.responses import ResponseTitle

TITLES = {
    400: ResponseTitle.VALIDATION_ERROR,
    404: ResponseTitle.NOT_FOUND,
    409: ResponseTitle.CONFLICT,
}


def _refuse(error: ClubError) -> ApiError:
    """One refusal, in the vocabulary the rest of the project uses.

    The status travels on the exception rather than being decided here, which is
    what keeps this transport's answer the same as the HTTP one's.
    """
    status = getattr(error, "status", 400)
    return ApiError(
        str(error), status=status, title=TITLES.get(status, ResponseTitle.VALIDATION_ERROR)
    )


def _caller(info: Info[Any, Any]) -> Any:
    """The account this query proves it is. Demanded: there is no public club."""
    return require_caller(caller(info.context.request))


def _run(call: Any, *args: Any, **kwargs: Any) -> Any:
    try:
        return call(*args, **kwargs)
    except ClubError as refusal:
        raise _refuse(refusal) from None


@strawberry.type
class Query:
    @strawberry.field(description="Every club this deployment runs, without their ladders.")
    @resolver
    def clubs(
        self, info: Info[Any, Any], limit: int | None = None, offset: int = 0
    ) -> list[ClubType]:
        _caller(info)
        return [club_type(row) for row in _run(club_service.clubs, limit=limit, offset=offset)]

    @strawberry.field(description="One club and every rung of its ladder, in order.")
    @resolver
    def club(self, info: Info[Any, Any], slug: str) -> ClubType:
        _caller(info)
        return club_type(_run(club_service.club, slug))

    @strawberry.field(description="One club's ladder.")
    @resolver
    def club_levels(self, info: Info[Any, Any], slug: str) -> list[LevelType]:
        _caller(info)
        return [level for row in _run(club_service.levels, slug) if (level := level_type(row))]

    @strawberry.field(
        description="Where this account stands: its club, its XP and the rung it is on."
    )
    @resolver
    def club_membership(self, info: Info[Any, Any]) -> MembershipType:
        return membership_type(_run(club_service.me, _caller(info)))

    @strawberry.field(description="This account's club's running missions, with its progress.")
    @resolver
    def club_missions(self, info: Info[Any, Any]) -> list[MissionType]:
        return [mission_type(row) for row in _run(club_service.missions, _caller(info))]

    @strawberry.field(description="Every XP this account has been paid, newest first.")
    @resolver
    def club_awards(
        self, info: Info[Any, Any], limit: int | None = None, offset: int = 0
    ) -> list[AwardType]:
        rows = _run(club_service.awards, _caller(info), limit=limit, offset=offset)
        return [award_type(row) for row in rows]

    @strawberry.field(description="How many awards `clubAwards` pages through, in total.")
    @resolver
    def club_award_count(self, info: Info[Any, Any]) -> int:
        return int(_run(club_service.award_count, _caller(info)))

    @strawberry.field(description="This account's own club, by XP. No other club's.")
    @resolver
    def club_leaderboard(self, info: Info[Any, Any], limit: int | None = None) -> list[RankType]:
        rows = _run(club_service.leaderboard, _caller(info), limit=limit)
        return [rank_type(row) for row in rows]

    @strawberry.field(
        description="What this deployment emits, and so what a mission can be built out of."
    )
    @resolver
    def club_events(self, info: Info[Any, Any]) -> list[EventType]:
        _caller(info)
        return [
            EventType(
                key=spec.key,
                label=spec.label,
                description=spec.description,
                value_label=spec.value_label,
                # The scalar is opaque to the type checker; the shape is documentation.
                fields=cast(JSON, dict(spec.fields)),
                source=spec.source,
            )
            for spec in sorted_specs()
        ]


@strawberry.type
class Mutation:
    @strawberry.mutation(description="Join a club. An account may be in exactly one at a time.")
    @resolver
    def club_join(self, info: Info[Any, Any], slug: str) -> MembershipType:
        return membership_type(_run(club_service.join, _caller(info), slug))

    @strawberry.mutation(
        description="Leave your club, keeping your XP and your history for a return."
    )
    @resolver
    def club_leave(self, info: Info[Any, Any]) -> MembershipType:
        return membership_type(_run(club_service.leave, _caller(info)))
