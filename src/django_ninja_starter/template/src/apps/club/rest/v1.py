"""The club over HTTP: what clubs exist, where you stand, and what you can earn.

Every decision is :class:`ClubService`'s. The one that shapes this file is that a
membership is derived from the caller and there is no parameter that widens it:
no endpoint takes a membership id, and the leaderboard is your own club's or
nothing. A club is a social object, and an API that let any account list any
club's members would be a directory of everybody who uses the deployment.

Nothing here completes a mission. There is no "claim" endpoint and no "I did it"
call, because a mission a client can report is a mission a client can invent --
the engine is told by the app where the thing actually happened, over
:func:`apps.club.track`, and decides for itself.
"""

from typing import Any

from django.http import HttpRequest
from ninja import Router
from ninja.errors import HttpError

from apps.club.errors import ClubError
from apps.club.events import sorted_specs
from apps.club.rest.schemas import (
    AwardPage,
    ClubOut,
    EventOut,
    JoinIn,
    LevelOut,
    MembershipOut,
    MissionOut,
    RankOut,
)
from apps.club.services import club_service

try:  # pragma: no cover - exercised by whichever branch the project installs
    from infrastructure.auth.core.sessions import api_auth
except ImportError:  # pragma: no cover - only in a project without the auth apps
    from ninja.security import django_auth as api_auth  # type: ignore[assignment]

router = Router(auth=api_auth)


def _run(call: Any, *args: Any, **kwargs: Any) -> Any:
    """Run one service call, translating its refusal into the status it carries.

    Every refusal this app makes is a :class:`ClubError` with a status on it, so
    the mapping lives on the exception rather than as a chain of `except` clauses
    each transport has to keep in step.
    """
    try:
        return call(*args, **kwargs)
    except ClubError as refusal:
        raise HttpError(refusal.status, str(refusal)) from None


@router.get("/clubs", response=list[ClubOut], summary="Every club this deployment runs")
def list_clubs(request: HttpRequest, limit: int | None = None, offset: int = 0) -> Any:
    """The clubs somebody could be in. Archived ones are not listed at all.

    Without levels: a list of ten clubs carrying ten ladders is a page nobody
    reads, and the ladder is what `GET /club/clubs/{slug}` is for.
    """
    return _run(club_service.clubs, limit=limit, offset=offset)


@router.get("/clubs/{slug}", response=ClubOut, summary="One club and its ladder")
def get_club(request: HttpRequest, slug: str) -> Any:
    """One club in full, including every rung in order.

    Readable by anybody signed in, whether or not they are in it: a ladder is
    what somebody looks at to decide whether to join.
    """
    return _run(club_service.club, slug)


@router.get("/clubs/{slug}/levels", response=list[LevelOut], summary="One club's ladder")
def list_levels(request: HttpRequest, slug: str) -> Any:
    return _run(club_service.levels, slug)


@router.get("/me", response=MembershipOut, summary="Where this account stands")
def me(request: HttpRequest) -> Any:
    """This account's club, its XP, the rung it is on and how far to the next.

    A 409 when the account is in no club, rather than an empty object: "you are
    not in a club" is a different thing from "you are in a club with nothing in
    it", and a client that had to tell them apart by inspecting fields would get
    it wrong.
    """
    return _run(club_service.me, request.user)


@router.post("/join", response=MembershipOut, summary="Join a club")
def join(request: HttpRequest, payload: JoinIn) -> Any:
    """Put this account in a club. It may be in exactly one.

    Joining the club you are already in returns that membership rather than
    refusing -- a double-tapped button is not an error. Joining a *different* one
    is a 409: leaving is a decision, and making it happen as a side effect of
    joining would let somebody lose a ladder they had spent months on by
    tapping the wrong card.

    An invite-only club refuses this and is joined from the back office instead.
    """
    return _run(club_service.join, request.user, payload.slug)


@router.post("/leave", response=MembershipOut, summary="Leave your club")
def leave(request: HttpRequest) -> Any:
    """Take this account out of its club, keeping its XP and its history.

    Rejoining later picks the ladder back up where it was left. A club where
    leaving reset your progress would be a club where the way out of a mistake
    is a thing people warn each other about.
    """
    return _run(club_service.leave, request.user)


@router.get("/missions", response=list[MissionOut], summary="What your club pays for")
def list_missions(request: HttpRequest) -> Any:
    """Your club's running missions, each with how far you have got.

    Only the ones actually live: a mission that is disabled, has not started or
    has ended is not listed, because a client showing it would be advertising XP
    nobody can earn.
    """
    return _run(club_service.missions, request.user)


@router.get("/awards", response=AwardPage, summary="Every XP you have been paid")
def list_awards(request: HttpRequest, limit: int | None = None, offset: int = 0) -> Any:
    """The award ledger for this account, newest first.

    Published rather than kept internal, because XP is derived from exactly these
    rows: a member can add them up and get the number the app reports, which is
    the point of deriving it.
    """
    awards = _run(club_service.awards, request.user, limit=limit, offset=offset)
    return {"awards": awards, "count": _run(club_service.award_count, request.user)}


@router.get("/leaderboard", response=list[RankOut], summary="Your club, by XP")
def leaderboard(request: HttpRequest, limit: int | None = None) -> Any:
    """The members of your own club, highest XP first, with your own row flagged.

    Your club's and no other's: there is no parameter here for the same reason
    there is no membership id anywhere else in this file.
    """
    return _run(club_service.leaderboard, request.user, limit=limit)


@router.get("/events", response=list[EventOut], summary="What a mission can be built out of")
def list_events(request: HttpRequest) -> Any:
    """Every event this deployment actually emits, as the apps installed declare them.

    Generated from the registry rather than written down, so it is honest: an app
    that is not installed contributes nothing, and an app of your own appears
    here the moment it registers its events. This is the list an operator picks
    from when writing a mission.
    """
    return [
        {
            "key": spec.key,
            "label": spec.label,
            "description": spec.description,
            "value_label": spec.value_label,
            "fields": spec.fields,
            "source": spec.source,
        }
        for spec in sorted_specs()
    ]
