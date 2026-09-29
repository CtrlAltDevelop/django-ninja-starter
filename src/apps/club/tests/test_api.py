"""The HTTP door: every route, and the scoping that keeps a club from being a directory.

The routes are thin, so most of what is worth asserting here is what they refuse:
no endpoint takes a membership id, the leaderboard is your own club's, and
nothing anywhere lets a client say it finished a mission.
"""

from typing import Any

import pytest
from django.test import Client

from apps.club.models import Club, Mission, Repeat
from apps.club.services import club_service, track
from apps.club.tests.conftest import bearer

pytestmark = pytest.mark.django_db

BASE = "/api/v1/club"


def data(response: Any) -> Any:
    """The envelope this project wraps every reply in."""
    return response.json()["data"]


def test_the_club_list_does_not_carry_every_ladder(club: Club, alice: Any) -> None:
    response = Client().get(f"{BASE}/clubs", **bearer(alice))

    assert response.status_code == 200
    assert [row["slug"] for row in data(response)] == ["explorers"]
    assert data(response)[0]["levels"] == []


def test_one_club_comes_with_its_ladder_in_order(club: Club, alice: Any) -> None:
    """What somebody reads to decide whether to join, so it needs no membership."""
    response = Client().get(f"{BASE}/clubs/explorers", **bearer(alice))

    rungs = data(response)["levels"]
    assert [rung["name"] for rung in rungs] == ["Bronze", "Silver", "Gold", "Platinum"]
    assert [rung["xp_required"] for rung in rungs] == [0, 100, 300, 600]


def test_a_club_that_does_not_exist_is_a_404(alice: Any) -> None:
    assert Client().get(f"{BASE}/clubs/nowhere", **bearer(alice)).status_code == 404


def test_joining_and_reading_where_you_stand(club: Club, alice: Any) -> None:
    joined = Client().post(
        f"{BASE}/join",
        {"slug": "explorers"},
        content_type="application/json",
        **bearer(alice),
    )
    assert joined.status_code == 200

    standing = data(Client().get(f"{BASE}/me", **bearer(alice)))
    assert standing["club"]["slug"] == "explorers"
    assert standing["level"]["name"] == "Bronze"
    assert standing["progress"] == 0.0


def test_an_account_in_no_club_is_refused_rather_than_given_an_empty_shape(
    alice: Any,
) -> None:
    response = Client().get(f"{BASE}/me", **bearer(alice))

    assert response.status_code == 409


def test_joining_a_second_club_is_refused(club: Club, other_club: Club, alice: Any) -> None:
    club_service.join(alice, club.slug)

    response = Client().post(
        f"{BASE}/join",
        {"slug": "voyagers"},
        content_type="application/json",
        **bearer(alice),
    )

    assert response.status_code == 409


def test_leaving_over_http(member: Any) -> None:
    response = Client().post(f"{BASE}/leave", **bearer(member))

    assert response.status_code == 200
    assert data(response)["status"] == "left"


def test_the_mission_list_shows_progress_and_hides_what_is_not_running(
    member: Any, club: Club, mission: Mission, test_event: str
) -> None:
    Mission.objects.create(
        club=club,
        code="off",
        title="Switched off",
        event=test_event,
        xp=10,
        is_enabled=False,
    )
    track(member, test_event, reference="one")

    rows = data(Client().get(f"{BASE}/missions", **bearer(member)))

    assert [row["code"] for row in rows] == ["first-thing"]
    assert rows[0]["completed"] is True
    assert rows[0]["available"] is False


def test_the_award_ledger_adds_up_to_the_reported_xp(
    member: Any, mission: Mission, test_event: str
) -> None:
    """The point of deriving XP: a member can check it."""
    track(member, test_event, reference="one")

    awards = data(Client().get(f"{BASE}/awards", **bearer(member)))["awards"]
    standing = data(Client().get(f"{BASE}/me", **bearer(member)))

    assert sum(award["xp"] for award in awards) == standing["xp"]


def test_the_leaderboard_is_your_own_club_and_flags_you(
    member: Any, bob: Any, club: Club, test_event: str
) -> None:
    bob.profile.display_name = "Bob the Builder"
    bob.profile.save()
    club_service.add_member(bob, club.slug)
    club_service.grant(bob, xp=200, reason="Ahead", reference="b")
    club_service.grant(member, xp=50, reason="Behind", reference="a")

    rows = data(Client().get(f"{BASE}/leaderboard", **bearer(member)))

    # A name to recognise somebody by, never the login: alice has none set.
    assert [row["username"] for row in rows] == ["Bob B.", "Member"]
    assert [row["position"] for row in rows] == [1, 2]
    assert [row["is_you"] for row in rows] == [False, True]


def test_the_leaderboard_does_not_list_another_club(
    member: Any, bob: Any, other_club: Club
) -> None:
    club_service.add_member(bob, other_club.slug)

    rows = data(Client().get(f"{BASE}/leaderboard", **bearer(member)))

    assert [row["username"] for row in rows] == ["Member"]


def test_the_event_list_is_generated_from_what_is_registered(alice: Any, test_event: str) -> None:
    rows = data(Client().get(f"{BASE}/events", **bearer(alice)))

    keys = {row["key"] for row in rows}
    assert test_event in keys
    assert "club.member.joined" in keys


def test_there_is_no_way_for_a_client_to_claim_a_mission(member: Any, mission: Mission) -> None:
    """A mission a client can report is a mission a client can invent."""
    for path in (
        f"{BASE}/missions/{mission.pk}/claim",
        f"{BASE}/missions/{mission.pk}/complete",
        f"{BASE}/awards",
    ):
        assert Client().post(path, **bearer(member)).status_code in (404, 405)


def test_every_route_needs_a_credential(club: Club) -> None:
    for path in ("/clubs", "/me", "/missions", "/awards", "/leaderboard", "/events"):
        assert Client().get(f"{BASE}{path}").status_code == 401


def test_a_repeating_mission_shows_as_available_again(
    member: Any, mission: Mission, test_event: str
) -> None:
    mission.repeat = str(Repeat.EVERY_TIME)
    mission.save()
    track(member, test_event, reference="one")

    rows = data(Client().get(f"{BASE}/missions", **bearer(member)))

    assert rows[0]["completions"] == 1
    assert rows[0]["available"] is True
