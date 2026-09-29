"""Folding entries into checkpoints, and the balance staying the same while it happens.

The archive exists so reading a balance does not get slower forever. The property
that matters is therefore not "a checkpoint was written" but "the number did not
change" -- so most of these read the balance, fold, and read it again.
"""

from datetime import timedelta
from decimal import Decimal
from typing import Any

import pytest
from django.test import override_settings
from django.utils import timezone

from apps.wallet.balances import archive_all, archive_wallet, due_for_archive
from apps.wallet.catalog import PaymentMethod
from apps.wallet.models import WalletCheckpoint
from apps.wallet.services import wallet_service

pytestmark = pytest.mark.django_db


def deposit(user: Any, amount: str, reference: str) -> dict:
    return wallet_service.deposit(user, amount=Decimal(amount), method="cash", reference=reference)


def test_folding_does_not_change_the_balance(alice: Any, cash: PaymentMethod) -> None:
    """The whole point. Everything else here is in service of this one."""
    for index in range(5):
        deposit(alice, "10", f"d{index}")
    before = wallet_service.balance(alice)["settled"]

    archive_wallet(wallet_service.wallet_for(alice), force=True)

    after = wallet_service.balance(alice)
    assert after["settled"] == before == Decimal("50.0000")
    # And it is now read from a checkpoint rather than by re-summing five rows.
    assert after["checkpoint_sequence"] == 1
    assert after["unarchived_entries"] == 0


def test_twenty_entries_trigger_a_fold_without_waiting_a_day(
    alice: Any, cash: PaymentMethod
) -> None:
    """The count trigger: a busy wallet should not carry a day of rows on every read."""
    wallet = wallet_service.wallet_for(alice)
    for index in range(19):
        deposit(alice, "1", f"d{index}")
    assert not due_for_archive(wallet)

    deposit(alice, "1", "d19")
    assert due_for_archive(wallet)


def test_a_day_old_entry_triggers_a_fold_without_twenty_of_them(
    alice: Any, cash: PaymentMethod
) -> None:
    """The age trigger: a quiet wallet still gets folded, once a day."""
    entry = deposit(alice, "5", "old")
    wallet = wallet_service.wallet_for(alice)
    assert not due_for_archive(wallet)

    wallet.entries.filter(pk=entry["id"]).update(created_at=timezone.now() - timedelta(days=2))
    assert due_for_archive(wallet)


def test_a_pending_entry_is_never_folded_however_old(
    alice: Any, card: PaymentMethod, cash: PaymentMethod
) -> None:
    """A checkpoint is a number written down.

    Folding in something still free to change would make that written number wrong
    later -- so an old pending entry holds its own run back, and the result says
    how many it left.
    """
    deposit(alice, "100", "settled")
    pending = wallet_service.deposit(
        alice, amount=Decimal("50"), method="card", reference="pending"
    )
    wallet = wallet_service.wallet_for(alice)
    wallet.entries.filter(pk=pending["id"]).update(created_at=timezone.now() - timedelta(days=30))

    result = archive_wallet(wallet, force=True)

    assert result.archived == 1
    assert result.skipped_pending == 1
    assert wallet.entries.get(pk=pending["id"]).checkpoint_id is None


def test_a_pending_entry_settling_later_still_counts_once(
    alice: Any, card: PaymentMethod, cash: PaymentMethod
) -> None:
    """The failure this design is built to avoid: counted by a checkpoint and again on its own."""
    deposit(alice, "100", "settled")
    pending = wallet_service.deposit(alice, amount=Decimal("100"), method="card", reference="p")
    wallet = wallet_service.wallet_for(alice)
    archive_wallet(wallet, force=True)

    wallet_service.settle(alice, pending["id"])

    assert wallet_service.balance(alice)["settled"] == Decimal("196.1600")


def test_folding_twice_folds_nothing_the_second_time(alice: Any, cash: PaymentMethod) -> None:
    for index in range(3):
        deposit(alice, "10", f"d{index}")
    wallet = wallet_service.wallet_for(alice)

    archive_wallet(wallet, force=True)
    second = archive_wallet(wallet, force=True)

    assert second.archived == 0
    assert WalletCheckpoint.objects.filter(wallet=wallet).count() == 1


def test_each_checkpoint_carries_the_running_balance(alice: Any, cash: PaymentMethod) -> None:
    """Not the sum of what it folded: reading a balance is one row plus the entries since.

    A checkpoint holding only its own run would mean walking back through every
    checkpoint ever cut, which is the problem the archive was meant to solve.
    """
    wallet = wallet_service.wallet_for(alice)
    deposit(alice, "100", "a")
    archive_wallet(wallet, force=True)
    deposit(alice, "50", "b")
    archive_wallet(wallet, force=True)

    checkpoints = list(wallet.checkpoints.order_by("sequence"))
    assert [c.sequence for c in checkpoints] == [1, 2]
    assert checkpoints[0].balance == Decimal("100.0000")
    assert checkpoints[1].balance == Decimal("150.0000")
    assert checkpoints[1].credited == Decimal("50.0000")


def test_a_failed_entry_is_folded_and_counts_for_nothing(
    alice: Any, card: PaymentMethod, cash: PaymentMethod
) -> None:
    """Terminal, so it can be archived. Not settled, so it is not money."""
    deposit(alice, "100", "good")
    bad = wallet_service.deposit(alice, amount=Decimal("500"), method="card", reference="bad")
    wallet_service.fail(alice, bad["id"], reason="declined")

    wallet = wallet_service.wallet_for(alice)
    result = archive_wallet(wallet, force=True)

    assert result.archived == 2
    assert wallet_service.balance(alice)["settled"] == Decimal("100.0000")
    assert result.checkpoint is not None
    assert result.checkpoint.balance == Decimal("100.0000")


@override_settings(WALLET_ARCHIVE_THRESHOLD=3)
def test_the_run_folds_every_wallet_that_is_due(alice: Any, bob: Any, cash: PaymentMethod) -> None:
    """One transaction per wallet, not one for the run.

    A lock held across ten thousand wallets is an outage, and a run that fails
    halfway should leave the wallets it already folded folded.
    """
    for index in range(3):
        deposit(alice, "10", f"a{index}")
    deposit(bob, "10", "b0")

    results = archive_all()

    folded = {result.wallet_id for result in results}
    assert wallet_service.wallet_for(alice).pk in folded
    # Bob has one entry and is not due, so his wallet was left alone.
    assert wallet_service.wallet_for(bob).pk not in folded


def test_the_command_reports_what_it_folded(alice: Any, cash: PaymentMethod) -> None:
    from io import StringIO

    from django.core.management import call_command

    for index in range(4):
        deposit(alice, "10", f"d{index}")

    out = StringIO()
    call_command("wallet_archive", "--force", stdout=out)
    printed = out.getvalue()

    assert "Folded 4 entries" in printed
    assert wallet_service.balance(alice)["checkpoint_sequence"] == 1


def test_a_reversed_entry_still_counts_after_it_is_folded(
    alice: object, card: PaymentMethod, cash: PaymentMethod
) -> None:
    """A reversal is two facts, and a checkpoint has to fold both or neither.

    The original goes on counting once it is marked `reversed` -- it happened --
    and the opposing entry is what undoes it. A checkpoint that folded only the
    opposing one would leave the wallet short by the amount of the original, for
    good, and nothing afterwards would say why.
    """
    deposit = wallet_service.deposit(alice, amount=Decimal("100"), method="card", reference="d")
    wallet_service.settle(alice, deposit["id"])
    deposit_2 = wallet_service.deposit(alice, amount=Decimal("50"), method="cash", reference="d2")
    wallet_service.reverse(alice, deposit["id"], reference="cb")

    before = wallet_service.balance(alice)["settled"]
    assert before == Decimal("50.0000")

    wallet = wallet_service.wallet_for(alice)
    result = archive_wallet(wallet, force=True)

    assert result.archived == 3
    assert wallet_service.balance(alice)["settled"] == before
    assert result.checkpoint is not None
    assert result.checkpoint.balance == Decimal("50.0000")
    assert deposit_2["id"] is not None
