"""The layer every transport shares, tested once here rather than four times.

`NotificationService` is where the decisions live -- the page cap, the filters,
"a notification you cannot see is missing rather than forbidden", and the
`state` frame that keeps a second device in step. HTTP, GraphQL, gRPC and the
socket then only have to be tested for translating those answers, not for
re-deciding them.
"""

from typing import Any
from uuid import uuid4

import pytest

from apps.notifications.events import notify_everyone, notify_user, notify_users
from apps.notifications.models import Audience, Level, Notification
from apps.notifications.services import (
    MAX_PAGE,
    NotificationNotFound,
)
from apps.notifications.services import (
    notification_service as service,
)

# -- listing --------------------------------------------------------------


def test_the_list_mixes_both_audiences_and_nobody_elses_mail(
    announcement: Notification, for_alice: Notification, for_bob: Notification, alice: Any
) -> None:
    subjects = {row["subject"] for row in service.list(alice)}

    assert subjects == {announcement.subject, for_alice.subject}


def test_every_row_carries_both_pieces_of_state(for_alice: Notification, alice: Any) -> None:
    row = service.list(alice)[0]

    assert row["read"] is False
    assert row["dismissed"] is False


def test_the_unread_filter_is_three_valued(
    announcement: Notification, for_alice: Notification, alice: Any
) -> None:
    """Unset is "everything", which is a different question from `unread=false`."""
    service.mark_read(alice, for_alice.pk)

    assert len(service.list(alice)) == 2
    assert [row["subject"] for row in service.list(alice, unread=True)] == [announcement.subject]
    assert [row["subject"] for row in service.list(alice, unread=False)] == [for_alice.subject]


def test_the_list_can_be_narrowed_to_one_level(alice: Any) -> None:
    notify_user(alice, "Fine", level="info")
    notify_user(alice, "Broken", level="error")

    assert [row["subject"] for row in service.list(alice, level="error")] == ["Broken"]


def test_the_list_can_be_narrowed_to_one_audience(
    announcement: Notification, for_alice: Notification, alice: Any
) -> None:
    rows = service.list(alice, audience="global")

    assert [row["subject"] for row in rows] == [announcement.subject]


def test_dismissed_rows_are_left_out_unless_asked_for(for_alice: Notification, alice: Any) -> None:
    service.dismiss(alice, for_alice.pk)

    assert service.list(alice) == []
    assert len(service.list(alice, include_dismissed=True)) == 1


def test_the_page_size_is_capped_however_large_a_caller_asks_for(alice: Any) -> None:
    """Otherwise one call can ask the database for the whole table."""
    Notification.objects.bulk_create(
        Notification(audience=Audience.USER, recipient=alice, subject=f"#{index}")
        for index in range(MAX_PAGE + 10)
    )

    assert len(service.list(alice, limit=5000)) == MAX_PAGE


def test_a_nonsense_page_size_falls_back_to_one_row_rather_than_none(
    for_alice: Notification, alice: Any
) -> None:
    assert len(service.list(alice, limit=0)) == 1
    assert len(service.list(alice, limit=-5)) == 1


def test_a_negative_offset_starts_at_the_beginning(for_alice: Notification, alice: Any) -> None:
    assert len(service.list(alice, offset=-10)) == 1


def test_paging_walks_the_list_without_repeating_a_row(alice: Any) -> None:
    Notification.objects.bulk_create(
        Notification(audience=Audience.USER, recipient=alice, subject=f"#{index}")
        for index in range(5)
    )

    first = {row["id"] for row in service.list(alice, limit=2, offset=0)}
    second = {row["id"] for row in service.list(alice, limit=2, offset=2)}

    assert first.isdisjoint(second)


def test_the_total_ignores_the_page_but_not_the_filters(
    announcement: Notification, for_alice: Notification, alice: Any
) -> None:
    assert service.count(alice) == 2
    assert service.count(alice, audience="global") == 1


def test_the_total_counts_what_the_same_filters_would_list(alice: Any) -> None:
    Notification.objects.bulk_create(
        Notification(audience=Audience.USER, recipient=alice, subject=f"#{index}")
        for index in range(7)
    )

    assert len(service.list(alice, limit=3)) == 3
    assert service.count(alice) == 7


# -- one at a time --------------------------------------------------------


def test_one_notification_can_be_fetched_by_id(for_alice: Notification, alice: Any) -> None:
    assert service.get(alice, for_alice.pk)["subject"] == for_alice.subject


def test_a_dismissed_notification_is_still_fetchable_by_id(
    for_alice: Notification, alice: Any
) -> None:
    """A link to something cleared away should open it, not 404."""
    service.dismiss(alice, for_alice.pk)

    assert service.get(alice, for_alice.pk)["dismissed"] is True


def test_fetching_somebody_elses_notification_is_missing_not_forbidden(
    for_bob: Notification, alice: Any
) -> None:
    with pytest.raises(NotificationNotFound):
        service.get(alice, for_bob.pk)


def test_fetching_one_that_does_not_exist_is_missing(alice: Any) -> None:
    with pytest.raises(NotificationNotFound):
        service.get(alice, uuid4())


@pytest.mark.parametrize("method", ["mark_read", "mark_unread", "dismiss", "restore"])
def test_every_change_refuses_another_accounts_mail_the_same_way(
    method: str, for_bob: Notification, alice: Any
) -> None:
    with pytest.raises(NotificationNotFound):
        getattr(service, method)(alice, for_bob.pk)


@pytest.mark.parametrize("method", ["mark_read", "mark_unread", "dismiss", "restore"])
def test_every_change_answers_the_same_shape(
    method: str, for_alice: Notification, alice: Any
) -> None:
    result = getattr(service, method)(alice, for_alice.pk)

    assert set(result) == {"id", "unread", "changed"}
    assert result["id"] == for_alice.pk


def test_marking_read_reports_the_new_badge_number(
    announcement: Notification, for_alice: Notification, alice: Any
) -> None:
    assert service.mark_read(alice, for_alice.pk) == {
        "id": for_alice.pk,
        "unread": 1,
        "changed": True,
    }


def test_repeating_a_change_is_success_with_changed_false(
    for_alice: Notification, alice: Any
) -> None:
    service.mark_read(alice, for_alice.pk)

    assert service.mark_read(alice, for_alice.pk)["changed"] is False


def test_unreading_puts_it_back_in_the_badge(for_alice: Notification, alice: Any) -> None:
    service.mark_read(alice, for_alice.pk)

    assert service.mark_unread(alice, for_alice.pk)["unread"] == 1


def test_dismissing_clears_it_from_both_the_tray_and_the_badge(
    for_alice: Notification, alice: Any
) -> None:
    assert service.dismiss(alice, for_alice.pk)["unread"] == 0
    assert service.list(alice) == []


def test_restoring_puts_it_back_in_the_tray_but_not_the_badge(
    for_alice: Notification, alice: Any
) -> None:
    service.dismiss(alice, for_alice.pk)

    assert service.restore(alice, for_alice.pk)["unread"] == 0
    assert len(service.list(alice)) == 1


# -- everything at once ---------------------------------------------------


def test_reading_everything_reports_how_much_it_changed(
    announcement: Notification, for_alice: Notification, alice: Any
) -> None:
    assert service.mark_all_read(alice) == {"count": 2, "unread": 0}
    assert service.mark_all_read(alice) == {"count": 0, "unread": 0}


def test_dismissing_everything_reports_how_much_it_cleared(
    announcement: Notification, for_alice: Notification, alice: Any
) -> None:
    assert service.dismiss_all(alice) == {"count": 2, "unread": 0}
    assert service.dismiss_all(alice) == {"count": 0, "unread": 0}


def test_the_badge_counts_only_undismissed_unread(
    announcement: Notification, for_alice: Notification, alice: Any
) -> None:
    assert service.unread_count(alice) == 2

    service.dismiss(alice, for_alice.pk)

    assert service.unread_count(alice) == 1


# -- sending --------------------------------------------------------------


def test_notify_user_addresses_one_account(alice: Any, bob: Any) -> None:
    notification = notify_user(alice, "Your export is ready", link="/exports/1")

    assert notification.audience == Audience.USER
    assert notification.recipient == alice
    assert service.unread_count(bob) == 0


def test_notify_user_defaults_the_level_rather_than_storing_an_empty_one(alice: Any) -> None:
    assert notify_user(alice, "Anything").level == Level.INFO


def test_notify_everyone_addresses_nobody_in_particular(alice: Any, bob: Any) -> None:
    notification = notify_everyone("Maintenance", level="warning")

    assert notification.recipient is None
    assert service.unread_count(alice) == service.unread_count(bob) == 1


def test_notify_users_sends_one_notification_each(alice: Any, bob: Any) -> None:
    """One row per recipient, because read state is per account."""
    created = notify_users([alice, bob], "Deploy finished", level="success")

    assert len(created) == 2
    assert service.unread_count(alice) == service.unread_count(bob) == 1


def test_notify_users_collapses_a_repeated_account(alice: Any) -> None:
    """A caller building the list from two overlapping queries should not send twice."""
    assert len(notify_users([alice, alice], "Once")) == 1
    assert service.unread_count(alice) == 1


def test_notify_users_with_nobody_sends_nothing(db: None) -> None:
    assert notify_users([], "Into the void") == []
    assert Notification.objects.count() == 0


def test_notify_users_carries_every_field_through(alice: Any) -> None:
    notification = notify_users(
        [alice], "Subject", body="Body", level="error", link="/x", data={"k": 1}
    )[0]

    assert (notification.body, notification.level, notification.link, notification.data) == (
        "Body",
        Level.ERROR,
        "/x",
        {"k": 1},
    )
