"""The club over gRPC.

Every call derives its account from invocation metadata, so there is no field a
client could set to read somebody else's standing or somebody else's club's
members -- the same rule the other two transports follow, enforced the same way.

Completing a mission is absent here as it is everywhere: the engine is told by
the app where the thing actually happened, over :func:`apps.club.track`, and a
transport that let a client report it would be a transport that let a client
invent it.

The calls are named so none collides with a message: the generator resolves
actions and messages out of one registry, so a ``Club`` call beside a ``Club``
message would leave the response type pointing at the call. Hence ``GetClub``,
``ListClubs`` and ``MyMembership``.
"""

from __future__ import annotations

from typing import Any

from asgiref.sync import sync_to_async
from django_socio_grpc import generics
from django_socio_grpc.decorators import grpc_action

from apps.club.errors import ClubError
from apps.club.grpc.serializers import Award, Club, Event, Level, Membership, Mission, Rank
from apps.club.services import club_service
from infrastructure.common.errors import ApiError
from infrastructure.common.grpc.errors import action, require_caller
from infrastructure.common.identity import grpc_caller
from infrastructure.common.responses import ResponseTitle

TITLES = {
    400: ResponseTitle.VALIDATION_ERROR,
    404: ResponseTitle.NOT_FOUND,
    409: ResponseTitle.CONFLICT,
}


def _pb2() -> Any:
    from apps.club.grpc import club_pb2

    return club_pb2


def _refuse(error: ClubError) -> ApiError:
    """One refusal, carrying the status the exception declared.

    Decided on the exception rather than here, so this door's answer cannot
    drift from the HTTP one's.
    """
    status = getattr(error, "status", 400)
    return ApiError(
        str(error), status=status, title=TITLES.get(status, ResponseTitle.VALIDATION_ERROR)
    )


def _text(value: Any) -> str:
    """A value the service may have left as ``None``, as the wire wants it."""
    return "" if value is None else str(value)


def _level(row: dict[str, Any] | None) -> Any:
    if row is None:
        return None
    return _pb2().Level(
        id=str(row["id"]),
        position=row["position"],
        name=row["name"],
        xp_required=row["xp_required"],
        logo=row["logo"],
        perks=row["perks"],
        metadata=row["metadata"],
    )


def _club(row: dict[str, Any]) -> Any:
    return _pb2().Club(
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
        created_at=row["created_at"],
        levels=[_level(level) for level in row.get("levels", [])],
    )


def _membership(row: dict[str, Any]) -> Any:
    return _pb2().Membership(
        id=str(row["id"]),
        club=_pb2().ClubBrief(
            id=str(row["club"]["id"]),
            name=row["club"]["name"],
            slug=row["club"]["slug"],
            logo=row["club"]["logo"],
        ),
        status=row["status"],
        xp=row["xp"],
        level=_level(row["level"]),
        next_level=_level(row["next_level"]),
        xp_to_next=row["xp_to_next"],
        progress=row["progress"],
        joined_at=row["joined_at"],
    )


def _mission(row: dict[str, Any]) -> Any:
    return _pb2().Mission(
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
        last_completed_at=_text(row["last_completed_at"]),
        starts_at=_text(row["starts_at"]),
        ends_at=_text(row["ends_at"]),
    )


def _award(row: dict[str, Any]) -> Any:
    return _pb2().Award(
        id=str(row["id"]),
        xp=row["xp"],
        reason=row["reason"],
        reference=row["reference"],
        mission_id=_text(row["mission_id"]),
        metadata=row["metadata"],
        created_at=row["created_at"],
    )


def _rank(row: dict[str, Any]) -> Any:
    return _pb2().Rank(
        position=row["position"],
        membership_id=str(row["membership_id"]),
        username=row["username"],
        xp=row["xp"],
        level=_level(row["level"]),
        is_you=row["is_you"],
    )


class ClubService(generics.GenericService):
    """One account's club: what exists, where it stands, and what it can earn."""

    @grpc_action(
        request=[
            {"name": "limit", "type": "int32"},
            {"name": "offset", "type": "int32"},
        ],
        request_name="ListClubsRequest",
        response=[{"name": "clubs", "cardinality": "repeated", "type": Club}],
        response_name="ClubList",
    )
    @action
    async def ListClubs(self, request: Any, context: Any) -> Any:
        """Every club this deployment runs, without their ladders."""
        require_caller(await grpc_caller(context))
        try:
            rows = await sync_to_async(club_service.clubs)(
                limit=request.limit or None, offset=request.offset or 0
            )
        except ClubError as refusal:
            raise _refuse(refusal) from None
        return _pb2().ClubList(clubs=[_club(row) for row in rows])

    @grpc_action(
        request=[{"name": "slug", "type": "string"}],
        request_name="ClubRequest",
        response=[{"name": "club", "type": Club}],
        response_name="ClubResult",
    )
    @action
    async def GetClub(self, request: Any, context: Any) -> Any:
        """One club and every rung of its ladder, in order."""
        require_caller(await grpc_caller(context))
        try:
            row = await sync_to_async(club_service.club)(request.slug)
        except ClubError as refusal:
            raise _refuse(refusal) from None
        return _pb2().ClubResult(club=_club(row))

    @grpc_action(
        request=[{"name": "slug", "type": "string"}],
        request_name="LevelsRequest",
        response=[{"name": "levels", "cardinality": "repeated", "type": Level}],
        response_name="LevelList",
    )
    @action
    async def Levels(self, request: Any, context: Any) -> Any:
        """One club's ladder."""
        require_caller(await grpc_caller(context))
        try:
            rows = await sync_to_async(club_service.levels)(request.slug)
        except ClubError as refusal:
            raise _refuse(refusal) from None
        return _pb2().LevelList(levels=[_level(row) for row in rows])

    @grpc_action(
        request=[],
        response=[{"name": "membership", "type": Membership}],
        response_name="MembershipResult",
    )
    @action
    async def MyMembership(self, request: Any, context: Any) -> Any:
        """Where this account stands: its club, its XP and the rung it is on."""
        user = require_caller(await grpc_caller(context))
        try:
            row = await sync_to_async(club_service.me)(user)
        except ClubError as refusal:
            raise _refuse(refusal) from None
        return _pb2().MembershipResult(membership=_membership(row))

    @grpc_action(
        request=[{"name": "slug", "type": "string"}],
        request_name="JoinRequest",
        response=[{"name": "membership", "type": Membership}],
        response_name="JoinResult",
    )
    @action
    async def Join(self, request: Any, context: Any) -> Any:
        """Join a club. An account may be in exactly one at a time."""
        user = require_caller(await grpc_caller(context))
        try:
            row = await sync_to_async(club_service.join)(user, request.slug)
        except ClubError as refusal:
            raise _refuse(refusal) from None
        return _pb2().JoinResult(membership=_membership(row))

    @grpc_action(
        request=[],
        response=[{"name": "membership", "type": Membership}],
        response_name="LeaveResult",
    )
    @action
    async def Leave(self, request: Any, context: Any) -> Any:
        """Leave your club, keeping your XP and your history for a return."""
        user = require_caller(await grpc_caller(context))
        try:
            row = await sync_to_async(club_service.leave)(user)
        except ClubError as refusal:
            raise _refuse(refusal) from None
        return _pb2().LeaveResult(membership=_membership(row))

    @grpc_action(
        request=[],
        response=[{"name": "missions", "cardinality": "repeated", "type": Mission}],
        response_name="MissionList",
    )
    @action
    async def Missions(self, request: Any, context: Any) -> Any:
        """This account's club's running missions, with its progress against each."""
        user = require_caller(await grpc_caller(context))
        try:
            rows = await sync_to_async(club_service.missions)(user)
        except ClubError as refusal:
            raise _refuse(refusal) from None
        return _pb2().MissionList(missions=[_mission(row) for row in rows])

    @grpc_action(
        request=[
            {"name": "limit", "type": "int32"},
            {"name": "offset", "type": "int32"},
        ],
        request_name="AwardsRequest",
        response=[{"name": "awards", "cardinality": "repeated", "type": Award}],
        response_name="AwardList",
    )
    @action
    async def Awards(self, request: Any, context: Any) -> Any:
        """Every XP this account has been paid, newest first.

        The ledger the reported XP is derived from, so a client can add it up
        and get the same number.
        """
        user = require_caller(await grpc_caller(context))
        try:
            rows = await sync_to_async(club_service.awards)(
                user, limit=request.limit or None, offset=request.offset or 0
            )
        except ClubError as refusal:
            raise _refuse(refusal) from None
        return _pb2().AwardList(awards=[_award(row) for row in rows])

    @grpc_action(
        request=[{"name": "limit", "type": "int32"}],
        request_name="LeaderboardRequest",
        response=[{"name": "ranks", "cardinality": "repeated", "type": Rank}],
        response_name="Leaderboard",
    )
    @action
    async def LeaderboardCall(self, request: Any, context: Any) -> Any:
        """This account's own club, by XP. There is no field naming another."""
        user = require_caller(await grpc_caller(context))
        try:
            rows = await sync_to_async(club_service.leaderboard)(user, limit=request.limit or None)
        except ClubError as refusal:
            raise _refuse(refusal) from None
        return _pb2().Leaderboard(ranks=[_rank(row) for row in rows])

    @grpc_action(
        request=[],
        response=[{"name": "events", "cardinality": "repeated", "type": Event}],
        response_name="EventList",
    )
    @action
    async def Events(self, request: Any, context: Any) -> Any:
        """What this deployment emits, and so what a mission can be built out of."""
        from apps.club.events import sorted_specs

        require_caller(await grpc_caller(context))
        return _pb2().EventList(
            events=[
                _pb2().Event(
                    key=spec.key,
                    label=spec.label,
                    description=spec.description,
                    value_label=spec.value_label,
                    fields=spec.fields,
                    source=spec.source,
                )
                for spec in sorted_specs()
            ]
        )


#: What `config.grpc` registers for this app. Named here rather than discovered
#: by scanning, so a class that is not meant to be served is not served by having
#: been defined.
GRPC_SERVICES = [ClubService]
