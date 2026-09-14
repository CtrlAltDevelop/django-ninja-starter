"""The same club over gRPC.

Every call reads its account out of ``authorization`` metadata, so there is no
field on any request that could name somebody else's membership or somebody
else's club's members. That is the property worth checking here.

``transactional_db`` throughout: the server answers on its own connection, and
rows sitting in the test's open transaction are not there yet as far as it is
concerned.
"""

from collections.abc import Callable
from typing import Any

import grpc
import pytest
from google.protobuf.empty_pb2 import Empty

from apps.club.grpc import club_pb2, club_pb2_grpc
from apps.club.models import Club, Mission
from apps.club.services import club_service, track
from apps.club.tests.conftest import access_token

Stub = club_pb2_grpc.ClubControllerStub


@pytest.fixture
def joined(transactional_db: None, alice: Any, club: Club) -> Any:
    """Alice, already in the club, on a connection the server can also see."""
    club_service.join(alice, club.slug)
    return alice


def test_the_club_list_arrives(
    transactional_db: None, alice: Any, club: Club, grpc_call: Callable[..., Any]
) -> None:
    reply = grpc_call(Stub, "ListClubs", club_pb2.ListClubsRequest(), token=access_token(alice))

    assert [row.slug for row in reply.clubs] == ["explorers"]


def test_one_club_carries_its_ladder_in_order(
    transactional_db: None, alice: Any, club: Club, grpc_call: Callable[..., Any]
) -> None:
    reply = grpc_call(
        Stub, "GetClub", club_pb2.ClubRequest(slug="explorers"), token=access_token(alice)
    )

    assert [rung.position for rung in reply.club.levels] == [1, 2, 3, 4]
    assert [rung.xp_required for rung in reply.club.levels] == [0, 100, 300, 600]


def test_where_an_account_stands(joined: Any, grpc_call: Callable[..., Any]) -> None:
    reply = grpc_call(Stub, "MyMembership", Empty(), token=access_token(joined))

    assert reply.membership.club.slug == "explorers"
    assert reply.membership.xp == 0
    assert reply.membership.level.name == "Bronze"
    assert reply.membership.next_level.name == "Silver"
    assert reply.membership.xp_to_next == 100


def test_joining_over_grpc(
    transactional_db: None, alice: Any, club: Club, grpc_call: Callable[..., Any]
) -> None:
    reply = grpc_call(
        Stub, "Join", club_pb2.JoinRequest(slug="explorers"), token=access_token(alice)
    )

    assert reply.membership.club.slug == "explorers"


def test_joining_a_second_club_is_refused(
    joined: Any, other_club: Club, grpc_call: Callable[..., Any]
) -> None:
    with pytest.raises(grpc.RpcError):
        grpc_call(Stub, "Join", club_pb2.JoinRequest(slug="voyagers"), token=access_token(joined))


def test_leaving_over_grpc(joined: Any, grpc_call: Callable[..., Any]) -> None:
    reply = grpc_call(Stub, "Leave", Empty(), token=access_token(joined))

    assert reply.membership.status == "left"


def test_missions_carry_the_callers_own_progress(
    joined: Any, mission: Mission, test_event: str, grpc_call: Callable[..., Any]
) -> None:
    track(joined, test_event, reference="one")

    reply = grpc_call(Stub, "Missions", Empty(), token=access_token(joined))

    assert [row.code for row in reply.missions] == ["first-thing"]
    assert reply.missions[0].completed is True


def test_the_award_ledger_adds_up_to_the_reported_xp(
    joined: Any, mission: Mission, test_event: str, grpc_call: Callable[..., Any]
) -> None:
    track(joined, test_event, reference="one")
    token = access_token(joined)

    awards = grpc_call(Stub, "Awards", club_pb2.AwardsRequest(), token=token)
    standing = grpc_call(Stub, "MyMembership", Empty(), token=token)

    assert sum(row.xp for row in awards.awards) == standing.membership.xp


def test_the_leaderboard_is_the_callers_own_club(
    joined: Any, bob: Any, other_club: Club, grpc_call: Callable[..., Any]
) -> None:
    club_service.add_member(bob, other_club.slug)

    reply = grpc_call(
        Stub,
        "LeaderboardCall",
        club_pb2.LeaderboardRequest(),
        token=access_token(joined),
    )

    assert [row.username for row in reply.ranks] == ["alice"]
    assert reply.ranks[0].is_you is True


def test_the_event_list_is_the_registry(
    transactional_db: None, alice: Any, test_event: str, grpc_call: Callable[..., Any]
) -> None:
    reply = grpc_call(Stub, "Events", Empty(), token=access_token(alice))

    assert test_event in {row.key for row in reply.events}


def test_an_unsigned_call_is_refused(
    transactional_db: None, club: Club, grpc_call: Callable[..., Any]
) -> None:
    with pytest.raises(grpc.RpcError):
        grpc_call(Stub, "MyMembership", Empty())


def test_the_service_publishes_no_call_that_completes_a_mission() -> None:
    """The engine is told by the app where the thing happened, never by a client."""
    published = set(club_pb2_grpc.ClubControllerStub.__dict__)

    assert not {name for name in published if "complete" in name.lower()}
    assert not {name for name in published if "claim" in name.lower()}
