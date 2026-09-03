"""Retention: the one thing in this app that deletes rows, and refuses to guess.

The command exists because notifications accumulate and nothing else removes
them. It is a command rather than a signal or a periodic task because deleting
history on a schedule nobody configured is exactly the surprise a starter
should not ship -- so the interesting tests here are the ones about it refusing.
"""

from datetime import timedelta
from io import StringIO
from typing import Any

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import override_settings
from django.utils import timezone

from apps.notifications.events import notify_user
from apps.notifications.models import Notification, NotificationReceipt, mark_read


def _age(notification: Notification, days: int) -> None:
    Notification.objects.filter(pk=notification.pk).update(
        created_at=timezone.now() - timedelta(days=days)
    )


def _run(*arguments: str) -> str:
    out = StringIO()
    call_command("notifications_prune", *arguments, stdout=out)
    return out.getvalue()


def test_nothing_is_deleted_without_a_window_configured(for_alice: Notification) -> None:
    """The default keeps everything: a project that never schedules this loses nothing."""
    with pytest.raises(CommandError) as refusal:
        _run()

    assert "DJANGO_NOTIFICATIONS_RETENTION_DAYS" in str(refusal.value)
    assert Notification.objects.count() == 1


@pytest.mark.parametrize("days", ["0", "-1"])
def test_a_window_of_zero_or_less_is_refused_rather_than_read_as_everything(
    days: str, for_alice: Notification
) -> None:
    with pytest.raises(CommandError):
        _run("--days", days)

    assert Notification.objects.count() == 1


def test_the_configured_window_is_used_when_no_argument_is_given(
    for_alice: Notification, alice: Any
) -> None:
    _age(for_alice, 400)

    with override_settings(NOTIFICATIONS_RETENTION_DAYS=30):
        _run()

    assert Notification.objects.count() == 0


def test_an_argument_overrides_the_configured_window(for_alice: Notification) -> None:
    _age(for_alice, 60)

    with override_settings(NOTIFICATIONS_RETENTION_DAYS=365):
        _run("--days", "30")

    assert Notification.objects.count() == 0


def test_only_what_is_older_than_the_window_goes(alice: Any) -> None:
    old = notify_user(alice, "Last year")
    _age(old, 400)
    kept = notify_user(alice, "Today")

    _run("--days", "30")

    assert list(Notification.objects.all()) == [kept]


def test_the_receipts_go_with_the_notifications(for_alice: Notification, alice: Any) -> None:
    mark_read(alice, for_alice)
    _age(for_alice, 400)

    _run("--days", "30")

    assert NotificationReceipt.objects.count() == 0


def test_it_reports_how_many_notifications_it_deleted(alice: Any) -> None:
    """Notifications, not cascaded rows: "3 deleted" should mean three of these."""
    for index in range(3):
        _age(notify_user(alice, f"#{index}"), 400)
        mark_read(alice, Notification.objects.get(subject=f"#{index}"))

    assert "Deleted 3 notification(s)" in _run("--days", "30")


def test_a_dry_run_counts_without_deleting(for_alice: Notification) -> None:
    _age(for_alice, 400)

    output = _run("--days", "30", "--dry-run")

    assert "Would delete 1 notification(s)" in output
    assert Notification.objects.count() == 1


def test_a_run_with_nothing_old_enough_says_so_and_changes_nothing(
    for_alice: Notification,
) -> None:
    assert "Deleted 0 notification(s)" in _run("--days", "30")
    assert Notification.objects.count() == 1
