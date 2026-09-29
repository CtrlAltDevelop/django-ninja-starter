"""What the club announces, and when.

After commit, always: a receiver that fired inside the transaction could
congratulate somebody on a level-up that then rolled back. `level_changed` is
the one an integration actually wants, so it is the one with the most tests.
"""

from typing import Any

import pytest

from apps.club import signals
from apps.club.models import Club, Mission, Repeat
from apps.club.services import club_service, track

pytestmark = pytest.mark.django_db


class Heard:
    """Collects what a signal carried, so a test can assert on the payload."""

    def __init__(self, signal: Any) -> None:
        self.signal = signal
        self.payloads: list[dict[str, Any]] = []

    def __enter__(self) -> "Heard":
        self.signal.connect(self._receive)
        return self

    def __exit__(self, *_: Any) -> None:
        self.signal.disconnect(self._receive)

    def _receive(self, sender: Any, **kwargs: Any) -> None:
        self.payloads.append(kwargs)


def test_joining_is_announced(
    alice: Any, club: Club, django_capture_on_commit_callbacks: Any
) -> None:
    with (
        Heard(signals.member_joined) as heard,
        django_capture_on_commit_callbacks(execute=True),
    ):
        club_service.join(alice, club.slug)

    assert heard.payloads[0]["membership"]["club"]["slug"] == "explorers"


def test_leaving_is_announced(member: Any, django_capture_on_commit_callbacks: Any) -> None:
    with (
        Heard(signals.member_left) as heard,
        django_capture_on_commit_callbacks(execute=True),
    ):
        club_service.leave(member)

    assert heard.payloads[0]["membership"]["status"] == "left"


def test_an_award_is_announced(member: Any, django_capture_on_commit_callbacks: Any) -> None:
    with (
        Heard(signals.xp_awarded) as heard,
        django_capture_on_commit_callbacks(execute=True),
    ):
        club_service.grant(member, xp=10, reason="Hello", reference="a")

    assert heard.payloads[0]["award"]["xp"] == 10


def test_crossing_a_rung_announces_the_level_change(
    member: Any, django_capture_on_commit_callbacks: Any
) -> None:
    with (
        Heard(signals.level_changed) as heard,
        django_capture_on_commit_callbacks(execute=True),
    ):
        club_service.grant(member, xp=100, reason="Up", reference="up")

    assert heard.payloads[0]["previous"]["name"] == "Bronze"
    assert heard.payloads[0]["current"]["name"] == "Silver"


def test_earning_without_crossing_a_rung_announces_no_level_change(
    member: Any, django_capture_on_commit_callbacks: Any
) -> None:
    """A badge that changed every time XP moved would be a badge nobody trusts."""
    with (
        Heard(signals.level_changed) as heard,
        django_capture_on_commit_callbacks(execute=True),
    ):
        club_service.grant(member, xp=10, reason="A little", reference="a")

    assert heard.payloads == []


def test_finishing_a_mission_is_announced(
    member: Any, mission: Mission, test_event: str, django_capture_on_commit_callbacks: Any
) -> None:
    with (
        Heard(signals.mission_completed) as heard,
        django_capture_on_commit_callbacks(execute=True),
    ):
        track(member, test_event, reference="one")

    assert heard.payloads[0]["mission"]["code"] == "first-thing"
    assert heard.payloads[0]["award"]["xp"] == 50


def test_nothing_is_announced_for_a_mission_still_in_progress(
    member: Any, club: Club, test_event: str, django_capture_on_commit_callbacks: Any
) -> None:
    Mission.objects.create(
        club=club,
        code="three",
        title="Three times",
        event=test_event,
        xp=30,
        target_count=3,
        repeat=str(Repeat.EVERY_TIME),
        is_enabled=True,
    )

    with (
        Heard(signals.mission_completed) as heard,
        django_capture_on_commit_callbacks(execute=True),
    ):
        track(member, test_event, reference="one")

    assert heard.payloads == []
