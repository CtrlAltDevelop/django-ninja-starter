"""Who can see what, and what each account has done with it.

The model layer is where "read" and "dismissed" are defined, and both are
per-account facts about a row that may be shared with everybody -- so most of
what is worth testing here is two accounts disagreeing about the same
notification and both being right.
"""

from datetime import timedelta
from typing import Any

import pytest
from django.utils import timezone

from apps.notifications.models import (
    Audience,
    Notification,
    NotificationReceipt,
    dismiss,
    dismiss_all,
    mark_all_read,
    mark_read,
    mark_unread,
    prune,
    receipts_for,
    restore,
    unread_count,
)

# -- visibility -----------------------------------------------------------


def test_a_broadcast_is_visible_to_everybody(
    announcement: Notification, alice: Any, bob: Any
) -> None:
    assert list(Notification.objects.visible_to(alice)) == [announcement]
    assert list(Notification.objects.visible_to(bob)) == [announcement]


def test_a_notification_addressed_to_one_account_is_visible_to_nobody_else(
    for_alice: Notification, bob: Any
) -> None:
    assert list(Notification.objects.visible_to(bob)) == []


def test_for_user_carries_both_pieces_of_state(for_alice: Notification, alice: Any) -> None:
    row = Notification.objects.for_user(alice).get()

    assert row.read_at is None
    assert row.dismissed_at is None


def test_with_read_state_still_answers_for_callers_outside_this_app(
    for_alice: Notification, alice: Any
) -> None:
    """Kept as an alias, since it was the published name before dismissing existed."""
    mark_read(alice, for_alice)

    assert Notification.objects.with_read_state(alice).get().read_at is not None


# -- reading --------------------------------------------------------------


def test_read_state_is_per_account_even_for_the_same_row(
    announcement: Notification, alice: Any, bob: Any
) -> None:
    """The reason read state is a second table and not a column."""
    mark_read(alice, announcement)

    assert unread_count(alice) == 0
    assert unread_count(bob) == 1


def test_marking_the_same_notification_read_twice_is_not_an_error(
    for_alice: Notification, alice: Any
) -> None:
    """A client may say so over the socket and again over HTTP; neither should fail."""
    assert mark_read(alice, for_alice) is True
    assert mark_read(alice, for_alice) is False
    assert NotificationReceipt.objects.count() == 1


def test_marking_read_does_not_move_a_timestamp_that_is_already_set(
    for_alice: Notification, alice: Any
) -> None:
    """Otherwise "when did they read it?" answers "the last time they clicked"."""
    mark_read(alice, for_alice)
    first = NotificationReceipt.objects.get().read_at

    mark_read(alice, for_alice)

    assert NotificationReceipt.objects.get().read_at == first


def test_reading_can_be_undone(for_alice: Notification, alice: Any) -> None:
    mark_read(alice, for_alice)

    assert mark_unread(alice, for_alice) is True
    assert unread_count(alice) == 1


def test_unreading_something_already_unread_changes_nothing(
    for_alice: Notification, alice: Any
) -> None:
    assert mark_unread(alice, for_alice) is False
    assert unread_count(alice) == 1


def test_unreading_something_never_touched_leaves_no_receipt_claiming_otherwise(
    for_alice: Notification, alice: Any
) -> None:
    """A no-op must not invent a receipt that says the row was read."""
    mark_unread(alice, for_alice)

    assert NotificationReceipt.objects.filter(read_at__isnull=False).count() == 0


def test_marking_everything_read_covers_both_audiences_at_once(
    announcement: Notification, for_alice: Notification, alice: Any
) -> None:
    assert mark_all_read(alice) == 2
    assert unread_count(alice) == 0


def test_marking_everything_read_with_nothing_unread_writes_nothing(alice: Any) -> None:
    assert mark_all_read(alice) == 0


def test_marking_everything_read_counts_only_what_was_outstanding(
    announcement: Notification, for_alice: Notification, alice: Any
) -> None:
    mark_read(alice, for_alice)

    assert mark_all_read(alice) == 1


def test_marking_everything_read_updates_a_receipt_that_already_existed(
    announcement: Notification, alice: Any
) -> None:
    """The bulk path has to cope with a row that was read and then unread again."""
    mark_read(alice, announcement)
    mark_unread(alice, announcement)

    assert mark_all_read(alice) == 1
    assert unread_count(alice) == 0


def test_marking_everything_read_leaves_other_accounts_alone(
    announcement: Notification, alice: Any, bob: Any
) -> None:
    mark_all_read(alice)

    assert unread_count(bob) == 1


# -- dismissing -----------------------------------------------------------


def test_dismissing_takes_it_out_of_the_tray_without_deleting_anything(
    announcement: Notification, alice: Any, bob: Any
) -> None:
    """A broadcast belongs to everybody: one person clearing it must not remove it."""
    assert dismiss(alice, announcement) is True

    assert list(Notification.objects.for_user(alice)) == []
    assert list(Notification.objects.for_user(bob)) == [announcement]
    assert Notification.objects.count() == 1


def test_dismissing_marks_it_read_so_the_badge_does_not_lie(
    for_alice: Notification, alice: Any
) -> None:
    dismiss(alice, for_alice)

    assert unread_count(alice) == 0


def test_a_dismissed_notification_is_still_reachable_when_asked_for(
    for_alice: Notification, alice: Any
) -> None:
    dismiss(alice, for_alice)

    assert list(Notification.objects.for_user(alice, include_dismissed=True)) == [for_alice]


def test_dismissing_twice_changes_nothing(for_alice: Notification, alice: Any) -> None:
    assert dismiss(alice, for_alice) is True
    assert dismiss(alice, for_alice) is False


def test_a_dismissal_can_be_undone(for_alice: Notification, alice: Any) -> None:
    dismiss(alice, for_alice)

    assert restore(alice, for_alice) is True
    assert list(Notification.objects.for_user(alice)) == [for_alice]


def test_restoring_leaves_read_state_alone(for_alice: Notification, alice: Any) -> None:
    """Putting something back in the tray is not the same as not having read it."""
    dismiss(alice, for_alice)
    restore(alice, for_alice)

    assert unread_count(alice) == 0


def test_restoring_something_never_dismissed_changes_nothing(
    for_alice: Notification, alice: Any
) -> None:
    assert restore(alice, for_alice) is False


def test_dismissing_everything_empties_the_tray_and_the_badge(
    announcement: Notification, for_alice: Notification, alice: Any
) -> None:
    assert dismiss_all(alice) == 2
    assert list(Notification.objects.for_user(alice)) == []
    assert unread_count(alice) == 0


def test_dismissing_everything_with_an_empty_tray_writes_nothing(alice: Any) -> None:
    assert dismiss_all(alice) == 0


def test_dismissing_everything_counts_read_ones_too(
    announcement: Notification, for_alice: Notification, alice: Any
) -> None:
    """ "Empty the tray" is about the tray, not about what is unread in it."""
    mark_all_read(alice)

    assert dismiss_all(alice) == 2


def test_dismissing_everything_leaves_other_accounts_alone(
    announcement: Notification, alice: Any, bob: Any
) -> None:
    dismiss_all(alice)

    assert list(Notification.objects.for_user(bob)) == [announcement]


# -- receipts -------------------------------------------------------------


def test_a_receipt_reports_its_own_state(for_alice: Notification, alice: Any) -> None:
    dismiss(alice, for_alice)
    receipt = NotificationReceipt.objects.get()

    assert receipt.is_read is True
    assert receipt.is_dismissed is True
    assert str(receipt) == f"alice / {for_alice.pk}"


def test_a_fresh_receipt_reports_neither(for_alice: Notification, alice: Any) -> None:
    mark_unread(alice, for_alice)
    receipt = NotificationReceipt.objects.get()

    assert receipt.is_read is False
    assert receipt.is_dismissed is False


def test_receipts_can_be_fetched_for_a_batch_in_one_query(
    announcement: Notification, for_alice: Notification, alice: Any
) -> None:
    mark_read(alice, for_alice)

    found = receipts_for(alice, [announcement, for_alice])

    assert set(found) == {for_alice.pk}


def test_an_account_reads_a_notification_only_once(for_alice: Notification, alice: Any) -> None:
    """The unique constraint underneath, not the helper that respects it."""
    from django.db import IntegrityError

    NotificationReceipt.objects.create(notification=for_alice, user=alice)

    with pytest.raises(IntegrityError):
        NotificationReceipt.objects.create(notification=for_alice, user=alice)


# -- the constraint -------------------------------------------------------


def test_a_user_notification_without_a_recipient_is_refused(db: None) -> None:
    """The constraint, not the form validation: this is the guarantee underneath."""
    with pytest.raises(Exception):  # noqa: B017 - the backend picks the exception class
        Notification.objects.create(audience=Audience.USER, recipient=None, subject="Nobody")


def test_a_broadcast_with_a_recipient_is_refused(alice: Any) -> None:
    with pytest.raises(Exception):  # noqa: B017 - the backend picks the exception class
        Notification.objects.create(
            audience=Audience.GLOBAL, recipient=alice, subject="Everybody, and also Alice"
        )


def test_the_admin_form_says_what_the_constraint_says(alice: Any) -> None:
    """`clean` exists so this is a field error rather than an IntegrityError."""
    from django.core.exceptions import ValidationError

    with pytest.raises(ValidationError) as refusal:
        Notification(audience=Audience.GLOBAL, recipient=alice, subject="Both").full_clean()

    assert "recipient" in refusal.value.message_dict


def test_a_user_notification_with_no_recipient_is_a_field_error_in_the_admin(db: None) -> None:
    from django.core.exceptions import ValidationError

    with pytest.raises(ValidationError) as refusal:
        Notification(audience=Audience.USER, recipient=None, subject="Nobody").full_clean()

    assert "recipient" in refusal.value.message_dict


def test_a_notification_knows_whether_it_is_a_broadcast(
    announcement: Notification, for_alice: Notification
) -> None:
    assert announcement.is_global is True
    assert for_alice.is_global is False


def test_deleting_an_account_takes_its_notifications_with_it(
    for_alice: Notification, announcement: Notification, alice: Any
) -> None:
    alice.delete()

    assert list(Notification.objects.all()) == [announcement]


def test_deleting_an_account_takes_its_receipts_with_it(
    announcement: Notification, alice: Any
) -> None:
    """Otherwise a broadcast keeps a receipt pointing at nobody."""
    mark_read(alice, announcement)
    alice.delete()

    assert NotificationReceipt.objects.count() == 0


# -- retention ------------------------------------------------------------


def test_pruning_deletes_what_is_older_than_the_cutoff(alice: Any) -> None:
    old = Notification.objects.create(audience=Audience.USER, recipient=alice, subject="Last year")
    Notification.objects.filter(pk=old.pk).update(created_at=timezone.now() - timedelta(days=400))
    kept = Notification.objects.create(audience=Audience.USER, recipient=alice, subject="Today")

    assert prune(timezone.now() - timedelta(days=30)) >= 1
    assert list(Notification.objects.all()) == [kept]


def test_pruning_takes_the_receipts_with_it(announcement: Notification, alice: Any) -> None:
    mark_read(alice, announcement)
    Notification.objects.update(created_at=timezone.now() - timedelta(days=400))

    prune(timezone.now() - timedelta(days=30))

    assert NotificationReceipt.objects.count() == 0


def test_pruning_with_nothing_old_enough_deletes_nothing(announcement: Notification) -> None:
    assert prune(timezone.now() - timedelta(days=30)) == 0
    assert Notification.objects.count() == 1
