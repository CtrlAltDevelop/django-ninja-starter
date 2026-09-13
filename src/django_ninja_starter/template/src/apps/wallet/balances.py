"""Reading a balance, and folding old entries into a checkpoint so reading stays cheap.

Two halves of the same idea.

**Reading.** A balance is the last checkpoint plus every settled entry written
since it. That is one indexed row and a bounded aggregate, rather than a sum over
everything the wallet has ever done -- and it is the same arithmetic whether the
wallet is a day old or five years old.

A caller is given more than one number, because there is more than one true
answer and collapsing them is how an app tells somebody they can spend money
that is not there:

``settled``
    What the wallet actually holds. Only ``done`` entries.

``incoming`` / ``outgoing``
    Money recorded and not yet confirmed, each way. Visible, and worth nothing.

``available``
    ``settled`` minus ``outgoing``: what may be spent right now. A pending
    withdrawal has not left yet, but the money behind it is spoken for, and a
    wallet that let it be spent twice would have to decide later which of the two
    payouts to fail.

``projected``
    ``settled`` plus ``incoming`` minus ``outgoing``: where the wallet lands if
    everything outstanding succeeds. The optimistic number, useful to show and
    never to authorise against.

**Archiving.** ``manage.py wallet_archive`` folds entries that can no longer
change into a checkpoint carrying the running balance, and stamps each folded
entry with it. Two triggers, and either is enough: an age -- run it daily and a
day's entries are folded -- and a count, so a wallet doing a hundred movements an
hour does not wait a day with a hundred rows to re-sum on every read.

A pending entry is never archived, however old. It is still free to change, and a
checkpoint is a number written down; folding one in would make the written number
wrong later. So an old pending entry holds its own run back, which is correct and
is also why :func:`archive_wallet` reports what it skipped.
"""

from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal
from typing import Any

from django.conf import settings
from django.db import transaction
from django.db.models import Max, Q, Sum
from django.utils import timezone

from apps.wallet.methods import Direction
from apps.wallet.models import (
    ZERO,
    EntryStatus,
    Wallet,
    WalletCheckpoint,
    WalletEntry,
)
from apps.wallet.money import money


@dataclass(frozen=True)
class Balance:
    """Everything true about a wallet's money at one instant.

    Frozen, and built in one pass: two fields read at different moments would be
    a pair of numbers that never held at the same time, which is exactly the bug
    a wallet cannot afford.
    """

    currency: str
    settled: Decimal
    incoming: Decimal
    outgoing: Decimal
    checkpoint_sequence: int
    unarchived_entries: int

    @property
    def available(self) -> Decimal:
        """What may be spent now: settled, less what pending withdrawals have claimed."""
        return money(self.settled - self.outgoing)

    @property
    def projected(self) -> Decimal:
        """Where this lands if everything outstanding succeeds. Never authorise against it."""
        return money(self.settled + self.incoming - self.outgoing)

    @property
    def has_pending(self) -> bool:
        return bool(self.incoming or self.outgoing)

    def payload(self) -> dict[str, Any]:
        """The shape every transport answers with, decided once."""
        return {
            "currency": self.currency,
            "settled": self.settled,
            "available": self.available,
            "incoming": self.incoming,
            "outgoing": self.outgoing,
            "projected": self.projected,
            "has_pending": self.has_pending,
            "checkpoint_sequence": self.checkpoint_sequence,
            "unarchived_entries": self.unarchived_entries,
        }


def latest_checkpoint(wallet: Wallet) -> WalletCheckpoint | None:
    """The most recent checkpoint on this wallet, or ``None`` before the first archive."""
    return wallet.checkpoints.order_by("-sequence").first()


def balance_of(wallet: Wallet) -> Balance:
    """The whole truth about one wallet's money, in three queries.

    Not wrapped in a transaction of its own: a caller that needs the number to
    hold still while it decides something takes the wallet's row lock first, and
    a caller that is only displaying it does not want the contention.
    """
    checkpoint = latest_checkpoint(wallet)
    base = checkpoint.balance if checkpoint else ZERO
    sequence = checkpoint.sequence if checkpoint else 0

    unarchived = wallet.entries.unarchived()
    settled = base + unarchived.counted().signed_total()
    pending = unarchived.pending()
    outstanding = pending.aggregate(
        incoming=Sum("amount", filter=Q(direction=str(Direction.CREDIT))),
        outgoing=Sum("amount", filter=Q(direction=str(Direction.DEBIT))),
    )
    return Balance(
        currency=wallet.currency,
        # Quantised here, at the edge, rather than trusted from the aggregate.
        # A database is free to widen a SUM -- SQLite hands back a value with a
        # dozen decimal places -- and a balance published as 115.9600000000000
        # is a number no interface should have to render and no client should
        # have to round. The stored amounts are exact; this is about what is
        # read back out of them.
        settled=money(settled),
        incoming=money(outstanding["incoming"]),
        outgoing=money(outstanding["outgoing"]),
        checkpoint_sequence=sequence,
        unarchived_entries=unarchived.count(),
    )


def archive_threshold() -> int:
    return int(getattr(settings, "WALLET_ARCHIVE_THRESHOLD", 20))


def archive_after() -> timedelta:
    return timedelta(days=int(getattr(settings, "WALLET_ARCHIVE_AFTER_DAYS", 1)))


def due_for_archive(wallet: Wallet, *, now: Any = None) -> bool:
    """Whether this wallet has enough settled history to be worth folding.

    Either trigger is enough: enough entries to slow a read, or an entry old
    enough that today's run is the day it gets folded.
    """
    archivable = wallet.entries.archivable()
    count = archivable.count()
    if not count:
        return False
    if count >= archive_threshold():
        return True
    oldest = archivable.order_by("created_at").values_list("created_at", flat=True).first()
    return bool(oldest and oldest <= (now or timezone.now()) - archive_after())


@dataclass(frozen=True)
class ArchiveResult:
    """What one wallet's archive run did, in the words the command prints."""

    wallet_id: Any
    archived: int
    skipped_pending: int
    checkpoint: WalletCheckpoint | None

    @property
    def cut_a_checkpoint(self) -> bool:
        return self.checkpoint is not None


def archive_wallet(wallet: Wallet, *, force: bool = False, now: Any = None) -> ArchiveResult:
    """Fold this wallet's unchangeable entries into one checkpoint.

    The whole thing runs inside the wallet's row lock, for the reason every write
    in this app does: a deposit settling halfway through would otherwise be
    counted by the checkpoint *and* left unarchived, and the wallet would be
    credited twice for it.

    ``force`` skips the age and count triggers -- what ``--force`` on the command
    passes, and what the tests use -- and nothing skips the pending rule.
    """
    moment = now or timezone.now()
    with transaction.atomic():
        locked = Wallet.objects.select_for_update().get(pk=wallet.pk)
        if not force and not due_for_archive(locked, now=moment):
            return ArchiveResult(
                wallet_id=locked.pk,
                archived=0,
                skipped_pending=locked.entries.unarchived().pending().count(),
                checkpoint=None,
            )

        archivable = list(locked.entries.archivable().order_by("created_at", "id"))
        if not archivable:
            return ArchiveResult(
                wallet_id=locked.pk,
                archived=0,
                skipped_pending=locked.entries.unarchived().pending().count(),
                checkpoint=None,
            )

        previous = latest_checkpoint(locked)
        credited = sum(
            (
                entry.amount
                for entry in archivable
                if entry.counts_towards_balance and entry.direction == str(Direction.CREDIT)
            ),
            ZERO,
        )
        debited = sum(
            (
                entry.amount
                for entry in archivable
                if entry.counts_towards_balance and entry.direction == str(Direction.DEBIT)
            ),
            ZERO,
        )
        checkpoint = WalletCheckpoint.objects.create(
            wallet=locked,
            # Computed rather than `previous.sequence + 1` alone, so a checkpoint
            # deleted by hand in the admin cannot make the next one collide.
            sequence=(locked.checkpoints.aggregate(top=Max("sequence"))["top"] or 0) + 1,
            balance=(previous.balance if previous else ZERO) + credited - debited,
            credited=credited,
            debited=debited,
            entry_count=len(archivable),
            created_at=moment,
        )
        WalletEntry.objects.filter(pk__in=[entry.pk for entry in archivable]).update(
            checkpoint=checkpoint
        )
        return ArchiveResult(
            wallet_id=locked.pk,
            archived=len(archivable),
            skipped_pending=locked.entries.unarchived()
            .filter(status=str(EntryStatus.PENDING))
            .count(),
            checkpoint=checkpoint,
        )


def archive_all(*, force: bool = False, now: Any = None) -> list[ArchiveResult]:
    """Run the archive over every wallet, one transaction each.

    One transaction per wallet rather than one for the run: a lock held across
    ten thousand wallets is an outage, and a run that fails halfway should leave
    the wallets it already folded folded.
    """
    moment = now or timezone.now()
    results = []
    for wallet_id in Wallet.objects.values_list("pk", flat=True).iterator():
        wallet = Wallet.objects.get(pk=wallet_id)
        result = archive_wallet(wallet, force=force, now=moment)
        if result.archived or result.cut_a_checkpoint:
            results.append(result)
    return results
