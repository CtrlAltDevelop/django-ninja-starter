"""Who can see what, and what "read" means when the same row is read by many."""

from typing import Any

import pytest

from apps.notifications.models import (
    Audience,
    Notification,
    NotificationReceipt,
    mark_all_read,
    mark_read,
    unread_count,
)


def test_a_broadcast_is_visible_to_everybody(
    announcement: Notification, alice: Any, bob: Any
) -> None:
    assert list(Notification.objects.visible_to(alice)) == [announcement]
    assert list(Notification.objects.visible_to(bob)) == [announcement]


def test_a_notification_addressed_to_one_account_is_visible_to_nobody_else(
    for_alice: Notification, bob: Any
) -> None:
    assert list(Notification.objects.visible_to(bob)) == []


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


def test_marking_everything_read_covers_both_audiences_at_once(
    announcement: Notification, for_alice: Notification, alice: Any
) -> None:
    assert mark_all_read(alice) == 2
    assert unread_count(alice) == 0


def test_marking_everything_read_with_nothing_unread_writes_nothing(alice: Any) -> None:
    assert mark_all_read(alice) == 0


def test_marking_everything_read_leaves_other_accounts_alone(
    announcement: Notification, alice: Any, bob: Any
) -> None:
    mark_all_read(alice)

    assert unread_count(bob) == 1


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


def test_deleting_an_account_takes_its_notifications_with_it(
    for_alice: Notification, announcement: Notification, alice: Any
) -> None:
    alice.delete()

    assert list(Notification.objects.all()) == [announcement]
