"""Reminders: which dates the dashboard raises, when, and when it stops.

The interesting cases are all about time rather than about storage. A reminder
is a lead time, so moving the date moves the warning; an overdue date keeps
warning rather than disappearing, because the one that did not get done is the
one that matters; and a yearly event ticked off this year has to come back next
year without anybody re-entering it.
"""

from datetime import date, timedelta

import pytest
from django.core.exceptions import ValidationError
from django.utils import timezone

from apps.cms.models import SiteEvent, SiteEventKind, due_events

pytestmark = pytest.mark.django_db

#: Today, rather than a date written down here. Ticking an event off records
#: which occurrence was settled, and it records the one that is next *now* -- so
#: a suite pinned to a fixed date would be testing a row that was already stale
#: when it was written, and would start failing on its own next spring.
TODAY = timezone.localdate()


def event(**overrides: object) -> SiteEvent:
    values: dict = {
        "name": "Renew the domain",
        "kind": SiteEventKind.RENEWAL,
        "happens_on": TODAY + timedelta(days=30),
        "remind_days_before": 14,
    }
    return SiteEvent.objects.create(**{**values, **overrides})


class TestTheReminderWindow:
    def test_a_date_beyond_the_lead_time_is_not_raised_yet(self) -> None:
        assert event().is_due(TODAY) is False

    def test_it_is_raised_once_the_lead_time_opens(self) -> None:
        assert event(happens_on=TODAY + timedelta(days=14)).is_due(TODAY) is True

    def test_a_date_today_is_raised(self) -> None:
        assert event(happens_on=TODAY).is_due(TODAY) is True

    def test_an_overdue_date_keeps_being_raised(self) -> None:
        """The reminder nobody acted on is the one worth showing."""
        overdue = event(happens_on=TODAY - timedelta(days=40))

        assert overdue.is_due(TODAY) is True
        assert overdue.is_overdue(TODAY) is True

    def test_the_warning_moves_with_the_date(self) -> None:
        """A lead time rather than a second date: the date moves constantly."""
        moved = event(happens_on=TODAY + timedelta(days=100))

        assert moved.reminder_starts_on == moved.happens_on - timedelta(days=14)

    def test_zero_days_means_on_the_day(self) -> None:
        on_the_day = event(happens_on=TODAY + timedelta(days=1), remind_days_before=0)

        assert on_the_day.is_due(TODAY) is False
        assert on_the_day.is_due(TODAY + timedelta(days=1)) is True


class TestTickingOff:
    def test_a_handled_event_stops_being_raised(self) -> None:
        handled = event(happens_on=TODAY, is_done=True)

        assert handled.is_due(TODAY) is False

    def test_reopening_raises_it_again(self) -> None:
        reopened = event(happens_on=TODAY, is_done=True)
        reopened.is_done = False
        reopened.save()

        assert reopened.is_due(TODAY) is True

    def test_it_records_which_occurrence_was_handled(self) -> None:
        handled = event(happens_on=TODAY, is_done=True)

        assert handled.handled_occurrence == TODAY


class TestYearlyEvents:
    def test_it_counts_down_to_this_years_date(self) -> None:
        anniversary = TODAY + timedelta(days=40)
        yearly = event(
            happens_on=anniversary.replace(year=anniversary.year - 6), repeats_yearly=True
        )

        assert yearly.next_date(TODAY) == anniversary

    def test_once_this_years_date_has_gone_by_it_rolls_to_next_year(self) -> None:
        passed = TODAY - timedelta(days=40)
        yearly = event(happens_on=passed.replace(year=passed.year - 6), repeats_yearly=True)

        assert yearly.next_date(TODAY) == passed.replace(year=passed.year + 1)

    def test_the_twenty_ninth_of_february_lands_on_the_twenty_eighth(self) -> None:
        """What every calendar does, and what everybody means by the same day."""
        yearly = event(happens_on=date(2024, 2, 29), repeats_yearly=True)

        assert yearly.next_date(date(2027, 1, 1)) == date(2027, 2, 28)

    def test_handling_this_years_does_not_silence_next_years(self) -> None:
        """The reminder nobody sees is the one for the renewal that lapses."""
        this_year = TODAY + timedelta(days=9)
        yearly = event(happens_on=this_year, repeats_yearly=True, is_done=True)

        assert yearly.is_due(TODAY) is False
        assert yearly.is_due(TODAY.replace(year=TODAY.year + 1)) is True

    def test_it_cannot_be_announced_more_than_a_year_out(self) -> None:
        with pytest.raises(ValidationError, match="more than a year"):
            event(repeats_yearly=True, remind_days_before=400)


class TestTheDashboardList:
    def test_it_returns_what_is_due_soonest_first(self) -> None:
        event(name="Later", happens_on=TODAY + timedelta(days=10))
        event(name="Sooner", happens_on=TODAY + timedelta(days=2))
        event(name="Far off", happens_on=TODAY + timedelta(days=90))

        assert [row.name for row in due_events(TODAY)] == ["Sooner", "Later"]

    def test_a_switched_off_event_is_left_out(self) -> None:
        event(happens_on=TODAY, is_active=False)

        assert due_events(TODAY) == []

    def test_an_overdue_event_comes_first(self) -> None:
        event(name="Soon", happens_on=TODAY + timedelta(days=1))
        event(name="Missed", happens_on=TODAY - timedelta(days=5))

        assert [row.name for row in due_events(TODAY)] == ["Missed", "Soon"]
