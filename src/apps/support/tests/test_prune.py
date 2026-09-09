"""Retention: what `support_prune` deletes, and everything it refuses to.

The refusals are most of this file on purpose. The command deletes a customer's
support history, so every guard that stops it doing that by accident -- no
window configured, a negative window, a ticket that is merely old rather than
closed, an upload somebody actually attached -- is worth a test of its own.
"""

from datetime import timedelta
from io import StringIO
from typing import Any

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import override_settings
from django.utils import timezone

from apps.support.models import (
    Status,
    Ticket,
    Upload,
    create_ticket,
    post_message,
    set_status,
)

pytestmark = pytest.mark.django_db


def run(*args: str, **options: Any) -> str:
    out = StringIO()
    call_command("support_prune", *args, stdout=out, **options)
    return out.getvalue()


def closed_days_ago(user: Any, days: int, subject: str = "Old complaint") -> Ticket:
    """A ticket settled and left alone for a while."""
    ticket = create_ticket(user, subject=subject)
    post_message(ticket, user, "It happened again.")
    set_status(ticket, str(Status.CLOSED))
    Ticket.objects.filter(pk=ticket.pk).update(closed_at=timezone.now() - timedelta(days=days))
    return ticket


def staged_days_ago(user: Any, days: int) -> Upload:
    upload = Upload.objects.create(
        owner=user, name="screenshot.png", url="/media/support/uploads/x.png", size=12
    )
    Upload.objects.filter(pk=upload.pk).update(created_at=timezone.now() - timedelta(days=days))
    return upload


# -- the guards -------------------------------------------------------------


def test_with_no_window_configured_it_refuses_rather_than_deleting_everything(
    client_user: Any,
) -> None:
    """Zero means "keep everything", which is the default and must stay safe."""
    closed_days_ago(client_user, 400)

    with pytest.raises(CommandError) as refusal:
        run()

    assert "No retention window is set" in str(refusal.value)
    assert Ticket.objects.count() == 1


def test_a_negative_window_is_refused(client_user: Any) -> None:
    with pytest.raises(CommandError) as refusal:
        run(days=-1)

    assert "cannot be negative" in str(refusal.value)


@override_settings(SUPPORT_RETENTION_DAYS=0)
def test_days_on_the_command_line_stands_in_for_the_setting(
    client_user: Any,
) -> None:
    closed_days_ago(client_user, 400)

    run(days=30)

    assert Ticket.objects.count() == 0


# -- what goes, and what stays ----------------------------------------------


@override_settings(SUPPORT_RETENTION_DAYS=30)
def test_a_closed_ticket_older_than_the_window_goes(client_user: Any) -> None:
    closed_days_ago(client_user, 90)

    run()

    assert Ticket.objects.count() == 0


@override_settings(SUPPORT_RETENTION_DAYS=30)
def test_a_closed_ticket_inside_the_window_stays(client_user: Any) -> None:
    closed_days_ago(client_user, 10)

    run()

    assert Ticket.objects.count() == 1


@override_settings(SUPPORT_RETENTION_DAYS=30)
def test_an_open_ticket_is_kept_however_old_it_is(client_user: Any) -> None:
    """Age is not agreement: an open ticket is somebody's unanswered question."""
    old = create_ticket(client_user, subject="Still waiting")
    Ticket.objects.filter(pk=old.pk).update(created_at=timezone.now() - timedelta(days=900))

    run()

    assert Ticket.objects.count() == 1


@override_settings(SUPPORT_RETENTION_DAYS=30)
def test_a_resolved_ticket_is_kept_because_resolved_is_only_the_desks_opinion(
    client_user: Any,
) -> None:
    ticket = create_ticket(client_user, subject="Said it was fixed")
    set_status(ticket, str(Status.RESOLVED))
    Ticket.objects.filter(pk=ticket.pk).update(
        resolved_at=timezone.now() - timedelta(days=900),
        created_at=timezone.now() - timedelta(days=900),
    )

    run()

    assert Ticket.objects.count() == 1


@override_settings(SUPPORT_RETENTION_DAYS=30)
def test_the_messages_go_with_the_ticket(client_user: Any) -> None:
    from apps.support.models import Message

    closed_days_ago(client_user, 90)
    assert Message.objects.exists()

    run()

    assert not Message.objects.exists()


# -- staged uploads ---------------------------------------------------------


@override_settings(SUPPORT_RETENTION_DAYS=30)
def test_an_upload_nobody_ever_attached_is_swept_up(client_user: Any) -> None:
    staged_days_ago(client_user, 30)

    run()

    assert Upload.objects.count() == 0


@override_settings(SUPPORT_RETENTION_DAYS=30)
def test_a_recently_staged_upload_is_left_alone(client_user: Any) -> None:
    """Somebody may still be filling in the message it belongs to."""
    staged_days_ago(client_user, 1)

    run()

    assert Upload.objects.count() == 1


@override_settings(SUPPORT_RETENTION_DAYS=30)
def test_an_attached_upload_is_never_swept_up_however_old(client_user: Any, ticket: Ticket) -> None:
    """The attachment points at it, and that is what keeps the claim unique."""
    upload = staged_days_ago(client_user, 900)
    post_message(ticket, client_user, "Here it is.", uploads=[upload])

    run()

    assert Upload.objects.count() == 1


@override_settings(SUPPORT_RETENTION_DAYS=30)
def test_the_upload_window_can_be_set_for_a_run(client_user: Any) -> None:
    staged_days_ago(client_user, 3)

    run(upload_days=1)

    assert Upload.objects.count() == 0


# -- saying what it did -----------------------------------------------------


@override_settings(SUPPORT_RETENTION_DAYS=30)
def test_a_dry_run_reports_and_deletes_nothing(client_user: Any) -> None:
    closed_days_ago(client_user, 90)
    staged_days_ago(client_user, 30)

    said = run("--dry-run")

    assert "Would delete 1 closed ticket" in said
    assert "1 unattached upload" in said
    assert Ticket.objects.count() == 1
    assert Upload.objects.count() == 1


@override_settings(SUPPORT_RETENTION_DAYS=30)
def test_it_counts_rows_rather_than_tickets(client_user: Any) -> None:
    """The cascade takes messages and participants, and the number says so."""
    closed_days_ago(client_user, 90)

    said = run()

    assert "Deleted" in said
    assert "rows for tickets closed" in said


@override_settings(SUPPORT_RETENTION_DAYS=30)
def test_with_nothing_to_do_it_still_says_so(client_user: Any) -> None:
    said = run()

    assert "Deleted 0 rows" in said
    assert "0 unattached uploads" in said
