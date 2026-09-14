"""Fixtures for the club tests: a club with a real ladder and real missions.

The app is optional, so its tests are too: a project that has not enabled it has
no club tables and no registered models, and importing one raises before pytest
can say anything useful -- so collection stops here instead, and the rest of that
project's suite runs as normal.
"""

from typing import Any

import pytest
from django.apps import apps as django_apps
from django.contrib.auth import get_user_model
from django.test import RequestFactory

CLUB_INSTALLED = django_apps.is_installed("apps.club")
collect_ignore_glob = [] if CLUB_INSTALLED else ["*"]

if CLUB_INSTALLED:
    from apps.club.events import EventSpec, register
    from apps.club.models import Club, ClubLevel, Mission, Repeat


def access_token(user: Any) -> str:
    """A real credential from the project's own issuer, not a hand-rolled JWT."""
    from infrastructure.auth.core.sessions import issue_credentials

    request = RequestFactory().post("/")
    return issue_credentials(request, user, method="password").access_token


def bearer(user: Any) -> dict[str, str]:
    return {"HTTP_AUTHORIZATION": f"Bearer {access_token(user)}"}


@pytest.fixture
def alice(db: None) -> Any:
    return get_user_model().objects.create_user(username="alice", email="alice@example.test")


@pytest.fixture
def bob(db: None) -> Any:
    return get_user_model().objects.create_user(username="bob", email="bob@example.test")


@pytest.fixture
def operator(db: None) -> Any:
    return get_user_model().objects.create_user(
        username="operator", email="ops@example.test", is_staff=True
    )


@pytest.fixture
def test_event(db: None) -> str:
    """An event registered for the tests, so they do not depend on which apps are installed."""
    key = "tests.thing.happened"
    register(
        EventSpec(
            key=key,
            label="A thing happened",
            description="Only exists for the tests.",
            value_label="How much it was worth",
            fields={"kind": "What sort of thing"},
            source="tests",
        )
    )
    return key


@pytest.fixture
def club(db: None) -> "Club":
    """A club with a four-rung ladder: 0, 100, 300, 600."""
    made = Club.objects.create(name="Explorers", slug="explorers")
    for position, (name, xp) in enumerate(
        [("Bronze", 0), ("Silver", 100), ("Gold", 300), ("Platinum", 600)], start=1
    ):
        ClubLevel.objects.create(club=made, position=position, name=name, xp_required=xp)
    return made


@pytest.fixture
def other_club(db: None) -> "Club":
    made = Club.objects.create(name="Voyagers", slug="voyagers")
    ClubLevel.objects.create(club=made, position=1, name="Start", xp_required=0)
    return made


@pytest.fixture
def mission(club: "Club", test_event: str) -> "Mission":
    """One enabled mission paying 50 XP the first time the test event happens."""
    return Mission.objects.create(
        club=club,
        code="first-thing",
        title="Do the thing",
        event=test_event,
        xp=50,
        repeat=str(Repeat.ONCE),
        is_enabled=True,
    )


@pytest.fixture
def member(alice: Any, club: "Club") -> Any:
    """Alice, already in the club, for tests about earning rather than joining."""
    from apps.club.services import club_service

    club_service.join(alice, club.slug)
    return alice
