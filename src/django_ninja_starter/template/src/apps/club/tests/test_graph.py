"""The same club over GraphQL.

Same service as the routes, so the same answers: the membership is the caller's
own and there is no argument that widens it, the leaderboard is their club's,
and a refusal arrives with the status the REST layer would have used -- in
``extensions``, since GraphQL answers 200 whatever happened.
"""

import json
from typing import Any

import pytest
from django.test import Client

from apps.club.models import Club, Mission
from apps.club.services import club_service, track
from apps.club.tests.conftest import access_token

pytestmark = pytest.mark.django_db

CLUBS = "{ clubs { slug name memberCount levels { name } } }"
CLUB = """
query($slug: String!) {
  club(slug: $slug) { slug levels { position name xpRequired } }
}
"""
MEMBERSHIP = """
{
  clubMembership {
    xp
    progress
    xpToNext
    club { slug }
    level { name }
    nextLevel { name }
  }
}
"""
MISSIONS = "{ clubMissions { code xp completed available completions } }"
AWARDS = "{ clubAwards { xp reason } }"
LEADERBOARD = "{ clubLeaderboard { position username xp isYou } }"
EVENTS = "{ clubEvents { key source } }"
JOIN = """
mutation($slug: String!) { clubJoin(slug: $slug) { club { slug } xp } }
"""
LEAVE = "mutation { clubLeave { status } }"


def graphql(query: str, user: Any = None, **variables: Any) -> dict[str, Any]:
    headers = {"HTTP_AUTHORIZATION": f"Bearer {access_token(user)}"} if user else {}
    response = Client().post(
        "/graphql",
        data={"query": query, "variables": variables},
        content_type="application/json",
        **headers,
    )
    assert response.status_code == 200, response.content
    return json.loads(response.content)


def refusal(body: dict[str, Any]) -> dict[str, Any]:
    assert body.get("errors"), body
    return dict(body["errors"][0]["extensions"])


def test_the_club_list_carries_no_ladder(club: Club, alice: Any) -> None:
    body = graphql(CLUBS, alice)

    assert body["data"]["clubs"][0]["slug"] == "explorers"
    assert body["data"]["clubs"][0]["levels"] == []


def test_one_club_carries_its_ladder_in_order(club: Club, alice: Any) -> None:
    body = graphql(CLUB, alice, slug="explorers")

    rungs = body["data"]["club"]["levels"]
    assert [rung["position"] for rung in rungs] == [1, 2, 3, 4]
    assert [rung["xpRequired"] for rung in rungs] == [0, 100, 300, 600]


def test_joining_and_reading_where_you_stand(club: Club, alice: Any) -> None:
    assert graphql(JOIN, alice, slug="explorers")["data"]["clubJoin"]["xp"] == 0

    standing = graphql(MEMBERSHIP, alice)["data"]["clubMembership"]
    assert standing["club"]["slug"] == "explorers"
    assert standing["level"]["name"] == "Bronze"
    assert standing["nextLevel"]["name"] == "Silver"
    assert standing["xpToNext"] == 100


def test_an_account_in_no_club_is_refused_with_the_same_status_as_http(alice: Any) -> None:
    assert refusal(graphql(MEMBERSHIP, alice))["status"] == 409


def test_joining_a_second_club_is_refused(club: Club, other_club: Club, alice: Any) -> None:
    club_service.join(alice, club.slug)

    assert refusal(graphql(JOIN, alice, slug="voyagers"))["status"] == 409


def test_a_club_that_does_not_exist_is_a_404(alice: Any) -> None:
    assert refusal(graphql(CLUB, alice, slug="nowhere"))["status"] == 404


def test_leaving_over_graphql(member: Any) -> None:
    assert graphql(LEAVE, member)["data"]["clubLeave"]["status"] == "left"


def test_missions_carry_the_callers_own_progress(
    member: Any, mission: Mission, test_event: str
) -> None:
    track(member, test_event, reference="one")

    rows = graphql(MISSIONS, member)["data"]["clubMissions"]
    assert rows[0]["code"] == "first-thing"
    assert rows[0]["completed"] is True
    assert rows[0]["available"] is False


def test_the_award_ledger_adds_up_to_the_reported_xp(
    member: Any, mission: Mission, test_event: str
) -> None:
    track(member, test_event, reference="one")

    awards = graphql(AWARDS, member)["data"]["clubAwards"]
    standing = graphql(MEMBERSHIP, member)["data"]["clubMembership"]
    assert sum(award["xp"] for award in awards) == standing["xp"]


def test_the_leaderboard_is_the_callers_own_club(
    member: Any, bob: Any, club: Club, other_club: Club
) -> None:
    club_service.add_member(bob, other_club.slug)

    rows = graphql(LEADERBOARD, member)["data"]["clubLeaderboard"]
    assert [row["username"] for row in rows] == ["alice"]
    assert rows[0]["isYou"] is True


def test_the_event_list_is_the_registry(alice: Any, test_event: str) -> None:
    keys = {row["key"] for row in graphql(EVENTS, alice)["data"]["clubEvents"]}

    assert test_event in keys


def test_nothing_here_answers_without_a_credential(club: Club) -> None:
    for query in (CLUBS, MEMBERSHIP, MISSIONS, AWARDS, LEADERBOARD, EVENTS):
        assert refusal(graphql(query))["status"] == 401


def test_there_is_no_mutation_that_completes_a_mission(member: Any, mission: Mission) -> None:
    """The engine is told by the app where the thing happened, never by a client."""
    body = graphql(
        "mutation($id: String!) { clubCompleteMission(missionId: $id) { xp } }",
        member,
        id=str(mission.pk),
    )

    assert body.get("errors"), "a mission-completing mutation should not exist"
