"""The admin: what the list columns say, and what the receipt log will not let you do."""

from typing import Any

import pytest
from django.contrib.admin.sites import AdminSite
from django.test import RequestFactory

from apps.notifications.admin import NotificationAdmin, NotificationReceiptAdmin
from apps.notifications.models import Notification, NotificationReceipt, mark_read


def _admin() -> NotificationAdmin:
    return NotificationAdmin(Notification, AdminSite())


def _row(notification: Notification) -> Notification:
    """Refetch through the admin's queryset, which is where the receipt count comes from."""
    request = RequestFactory().get("/")
    return _admin().get_queryset(request).get(pk=notification.pk)


def test_the_audience_column_says_who_a_notification_is_for(
    announcement: Notification, for_alice: Notification
) -> None:
    assert _admin().audience_label(announcement) == "Everyone"
    assert _admin().audience_label(for_alice) == "One account"


def test_the_level_column_is_coloured_by_level(for_alice: Notification) -> None:
    assert "#2563eb" in _admin().level_badge(for_alice)


def test_a_level_with_no_colour_still_renders(for_alice: Notification) -> None:
    for_alice.level = "whatever"

    assert "#6b7280" in _admin().level_badge(for_alice)


def test_a_direct_notification_reads_as_read_or_unread(for_alice: Notification, alice: Any) -> None:
    assert _admin().read_by(_row(for_alice)) == "Unread"

    mark_read(alice, for_alice)

    assert _admin().read_by(_row(for_alice)) == "Read"


def test_a_broadcast_reads_as_a_count_because_there_is_no_roster(
    announcement: Notification, alice: Any, bob: Any
) -> None:
    assert _admin().read_by(_row(announcement)) == "0 accounts"

    mark_read(alice, announcement)

    assert _admin().read_by(_row(announcement)) == "1 account"


def test_receipts_are_a_log_and_cannot_be_written_by_hand() -> None:
    receipts = NotificationReceiptAdmin(NotificationReceipt, AdminSite())
    request = RequestFactory().get("/")

    assert receipts.has_add_permission(request) is False
    assert receipts.has_change_permission(request) is False


def test_what_the_receipt_log_declares_is_what_it_enforces() -> None:
    """`read_only_admin` is what the generated documentation reads, and a flag
    that drifted from the methods beside it would make that page a lie."""
    receipts = NotificationReceiptAdmin(NotificationReceipt, AdminSite())
    request = RequestFactory().get("/")

    assert receipts.read_only_admin is True
    assert not receipts.has_add_permission(request)
    assert not receipts.has_change_permission(request)


def test_the_receipt_list_query_carries_its_relations(for_alice: Notification, alice: Any) -> None:
    """Otherwise the changelist is one query per row, twice over."""
    mark_read(alice, for_alice)
    receipts = NotificationReceiptAdmin(NotificationReceipt, AdminSite())

    query = receipts.get_queryset(RequestFactory().get("/")).query

    assert "notification" in query.select_related
    assert "user" in query.select_related


def test_a_notification_is_named_by_its_subject(for_alice: Notification, alice: Any) -> None:
    mark_read(alice, for_alice)

    assert str(for_alice) == "Your export is ready"
    assert str(NotificationReceipt.objects.get()).startswith("alice read ")


def test_a_user_notification_with_no_recipient_is_a_field_error(db: None) -> None:
    from django.core.exceptions import ValidationError

    with pytest.raises(ValidationError) as refusal:
        Notification(audience="user", subject="Nobody").full_clean()

    assert "recipient" in refusal.value.message_dict
