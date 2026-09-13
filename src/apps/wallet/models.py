"""A wallet per account, the entries that moved it, and the checkpoints that sum them.

This app is meant to be lifted out and dropped into another Django project, so
it owns everything it needs and asks the project for almost nothing: settings it
defaults for itself, a router mount, and a scheduled command.

**There is no balance column.** That is the one decision everything else here
follows from, and it is worth saying why, because a column is the obvious design
and it is wrong in a way that is expensive to discover. A stored balance is a
second copy of a fact the entries already hold, and the two disagree the first
time a process dies between writing an entry and updating the column -- or the
first time two requests read the same balance, add to it, and write, and one of
the two additions is simply gone. Recovering from that means recomputing from
the entries, which is to say: the entries were the balance all along.

So the balance is derived, and three rows make that affordable:

A **wallet** is the account's identity in this app -- one per account, its
currency, and whether it may move at all.

An **entry** is one movement: an amount, a direction, the rail it came in on,
and a *status*. Status is the point. An entry that has been recorded is not
money that has arrived: a card deposit is pending until the processor confirms
it, and a pending entry that is counted as balance is money the wallet does not
have. A ``PENDING`` entry moves nothing. Everything unsettled is visible,
auditable, and worth nothing -- which is why a caller is shown two numbers
rather than one, and why :class:`Balance` has a field for each.

A **checkpoint** is an archived run of entries collapsed into their sum. Summing
every entry a wallet has ever had is correct and gets slower forever, so a
scheduled command folds settled entries into a checkpoint -- daily, or sooner
once enough have piled up -- and the balance becomes *the last checkpoint, plus
the entries written since it*. The entries are not deleted; they are stamped
with the checkpoint that counted them, so history stays complete and no entry
can be counted twice.

A **charge** is one fee as it was actually taken -- the commission, the tax on
the commission, the chain's own fee -- copied onto the entry rather than looked
up from the fee table later. The fee table is what we charge *now*; a charge row
is what somebody was charged *then*, and an operator renegotiating a commission
must not be able to rewrite last month's receipts.

Two things an entry carries that a simpler ledger would not:

*The money is three numbers, not one.* ``gross_amount`` is what the customer
asked for, ``fee_total`` is what it cost them, and ``amount`` is what actually
moved the balance -- after charges, and after conversion into the wallet's own
currency. Only the last is what a balance is made of; the first two are there so
the record means something to the person reading it.

*Approval is a second axis.* ``status`` is what the money is doing and
``approval`` is what a person decided, and they are genuinely independent: a
deposit can be waiting on the bank and waiting on the back office at the same
time. A movement through a method configured to require approval is a *request*
until an operator applies it, and cannot settle before they do. See
:class:`Approval`.

Concurrency is handled by locking the wallet row, not by trusting arithmetic:
every write that depends on a balance it just read takes ``select_for_update``
on the wallet first, so two withdrawals racing for the last ten units are
serialised and the second one sees what the first one did. See
:func:`apps.wallet.services.WalletService` for where that lock is taken.

What money *costs* and what it *converts at* are not here at all: they are
configuration an administrator edits, and they live in
:mod:`apps.wallet.catalog`, priced by :mod:`apps.wallet.charges`.
"""

import uuid
from decimal import Decimal
from typing import Any

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator
from django.db import models
from django.db.models import Q, Sum
from django.utils import timezone

from apps.wallet.catalog import ChargeKind, MethodFee, MethodNetwork, PaymentMethod
from apps.wallet.methods import Direction, Method, spec
from apps.wallet.money import MONEY, PERCENT, RATE, ZERO, money


class WalletStatus(models.TextChoices):
    """Whether a wallet may move, and which way.

    ``FROZEN`` is the interesting one: it refuses withdrawals and accepts
    deposits, which is what a compliance hold actually means. Refusing both
    would strand money that is already on its way in.
    """

    ACTIVE = "active", "Active"
    FROZEN = "frozen", "Frozen"
    CLOSED = "closed", "Closed"


class EntryKind(models.TextChoices):
    """What the movement *was*, independently of which way it went.

    Direction is derived from this rather than stored beside it, because the two
    cannot disagree if only one of them exists: a refund is a credit, always, and
    a row claiming to be a debit refund would be a bug nobody could resolve.
    """

    DEPOSIT = "deposit", "Deposit"
    WITHDRAWAL = "withdrawal", "Withdrawal"
    TRANSFER_IN = "transfer_in", "Transfer in"
    TRANSFER_OUT = "transfer_out", "Transfer out"
    REFUND = "refund", "Refund"
    CHARGEBACK = "chargeback", "Chargeback"
    PAYMENT = "payment", "Payment"
    FEE = "fee", "Fee"
    BONUS = "bonus", "Bonus"
    ADJUSTMENT_CREDIT = "adjustment_credit", "Adjustment in"
    ADJUSTMENT_DEBIT = "adjustment_debit", "Adjustment out"


#: Which way each kind moves the balance. Exhaustive on purpose: a kind added
#: without an entry here raises when it is first used, rather than defaulting to
#: a direction and quietly paying somebody.
KIND_DIRECTIONS: dict[str, str] = {
    str(EntryKind.DEPOSIT): str(Direction.CREDIT),
    str(EntryKind.TRANSFER_IN): str(Direction.CREDIT),
    str(EntryKind.REFUND): str(Direction.CREDIT),
    str(EntryKind.BONUS): str(Direction.CREDIT),
    str(EntryKind.ADJUSTMENT_CREDIT): str(Direction.CREDIT),
    str(EntryKind.WITHDRAWAL): str(Direction.DEBIT),
    str(EntryKind.TRANSFER_OUT): str(Direction.DEBIT),
    str(EntryKind.CHARGEBACK): str(Direction.DEBIT),
    str(EntryKind.PAYMENT): str(Direction.DEBIT),
    str(EntryKind.FEE): str(Direction.DEBIT),
    str(EntryKind.ADJUSTMENT_DEBIT): str(Direction.DEBIT),
}


def direction_of(kind: str) -> str:
    """Which way a kind moves the balance."""
    try:
        return KIND_DIRECTIONS[kind]
    except KeyError:
        raise ValueError(f"No direction declared for wallet entry kind {kind!r}.") from None


class EntryStatus(models.TextChoices):
    """Where one movement has got to.

    ``DONE`` is money, and so is ``REVERSED`` -- see :data:`COUNTED`, which is
    the one subtle thing here. The rest are states a movement passes through or
    ends in, and an app that treated any of them as balance would be crediting
    an account for a card payment that was later declined.
    """

    PENDING = "pending", "Pending"
    """Recorded, and waiting on something outside this app to confirm it."""

    DONE = "done", "Done"
    """Settled. The ordinary way an entry becomes part of a balance."""

    FAILED = "failed", "Failed"
    """The rail refused it. Terminal, and worth nothing."""

    CANCELLED = "cancelled", "Cancelled"
    """Withdrawn before it settled, by the account or by an operator."""

    EXPIRED = "expired", "Expired"
    """Never confirmed inside the window, so it was given up on."""

    REVERSED = "reversed", "Reversed"
    """It settled and was then undone -- a chargeback, a returned transfer.

    Reversal is a state change *plus* an opposing entry, never an edit of the
    original: the original really did happen, and a ledger that rewrites what
    happened cannot be reconciled against the rail that remembers it.
    """


#: The one status that counts towards a balance.
SETTLED = str(EntryStatus.DONE)

#: The statuses that make up a balance -- which is *not* the same as "settled",
#: and the difference is the one subtle thing in this file.
#:
#: A reversed entry still counts. It has to: reversal is a state change **plus an
#: opposing entry**, and if marking the original ``reversed`` also removed it from
#: the balance, the opposing entry would take the money away a second time. The
#: original really did happen -- that is the whole reason it is not deleted -- so
#: it goes on counting, and what undoes it is the entry written to undo it.
#:
#: Read ``reversed`` as "this happened, and its counterpart happened too", rather
#: than as "this did not happen".
COUNTED = frozenset({str(EntryStatus.DONE), str(EntryStatus.REVERSED)})

#: Statuses nothing can move out of. A checkpoint may only archive these -- a
#: pending entry folded into a sum would be a number that changes after it was
#: written down.
TERMINAL = frozenset(
    {
        str(EntryStatus.DONE),
        str(EntryStatus.FAILED),
        str(EntryStatus.CANCELLED),
        str(EntryStatus.EXPIRED),
        str(EntryStatus.REVERSED),
    }
)

#: What a pending entry is allowed to become, and what a settled one is. Written
#: as a map rather than as branches at each call site, so "can this move?" has
#: one answer and the admin, the API and the services all read it.
TRANSITIONS: dict[str, frozenset[str]] = {
    str(EntryStatus.PENDING): frozenset(
        {
            str(EntryStatus.DONE),
            str(EntryStatus.FAILED),
            str(EntryStatus.CANCELLED),
            str(EntryStatus.EXPIRED),
        }
    ),
    str(EntryStatus.DONE): frozenset({str(EntryStatus.REVERSED)}),
    str(EntryStatus.FAILED): frozenset(),
    str(EntryStatus.CANCELLED): frozenset(),
    str(EntryStatus.EXPIRED): frozenset(),
    str(EntryStatus.REVERSED): frozenset(),
}


class Approval(models.TextChoices):
    """Whether an operator has applied a movement, where one has to.

    Separate from :class:`EntryStatus` on purpose, and the separation is the
    whole design of the request flow. Status is what the *money* is doing;
    approval is what a *person* decided. A deposit can be pending because the
    bank has not confirmed it and, quite independently, because nobody in the
    back office has looked at it yet -- and collapsing the two into one field
    forces a choice about which of the two facts to lose.

    Kept apart, the rule is one sentence: a movement awaiting approval cannot
    settle. It can still fail, be cancelled or expire, because those need no
    permission -- refusing money is never the dangerous direction.
    """

    NOT_REQUIRED = "not_required", "Not required"
    """The method does not ask for one, so the rail's own confirmation is enough."""

    REQUESTED = "requested", "Awaiting approval"
    """The account asked. It is a request and nothing else until somebody applies it."""

    APPROVED = "approved", "Approved"
    """An operator applied it. It may now settle when the money does."""

    REJECTED = "rejected", "Rejected"
    """An operator refused it. Terminal, and the entry is cancelled with it."""


#: Approval states an entry can still settle from. Anything else is waiting on a
#: person, or has already been told no.
CLEARED = frozenset({str(Approval.NOT_REQUIRED), str(Approval.APPROVED)})


class Wallet(models.Model):
    """One account's money: its currency, whether it may move, and nothing else.

    Deliberately thin. Everything a caller wants from a wallet -- what is in it,
    what is on its way, what it may spend -- is computed from the entries and the
    checkpoints, because a column holding any of those would be a second copy of
    a fact that can drift from the first.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="wallet",
        help_text="The account this wallet belongs to. Exactly one each.",
    )
    currency = models.CharField(
        max_length=3,
        help_text=(
            "ISO 4217. Amounts are bare decimals, so this is the only record of what they mean."
        ),
    )
    status = models.CharField(
        max_length=16,
        choices=WalletStatus.choices,
        default=str(WalletStatus.ACTIVE),
        help_text="Frozen refuses withdrawals and still accepts deposits.",
    )
    created_at = models.DateTimeField(default=timezone.now, editable=False)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["status"])]

    def __str__(self) -> str:
        return f"{self.user} ({self.currency})"

    @property
    def is_active(self) -> bool:
        return self.status == str(WalletStatus.ACTIVE)

    @property
    def accepts_credit(self) -> bool:
        """A closed wallet takes nothing; a frozen one still takes money in."""
        return self.status != str(WalletStatus.CLOSED)

    @property
    def accepts_debit(self) -> bool:
        return self.status == str(WalletStatus.ACTIVE)


class WalletCheckpoint(models.Model):
    """A run of archived entries, collapsed into the balance they left behind.

    Written by ``manage.py wallet_archive`` and by nothing else. Each one carries
    the *running* balance as at the moment it was cut -- not the sum of the
    entries it archived -- so reading a balance is one row plus whatever has
    happened since, rather than a walk back through every checkpoint ever cut.

    ``sequence`` is per wallet and gapless, which is what makes a missing
    checkpoint visible: a balance derived from checkpoint 7 when 8 exists is a
    stale read, and a unique constraint is cheaper than discovering that from a
    customer.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    wallet = models.ForeignKey(Wallet, on_delete=models.CASCADE, related_name="checkpoints")
    sequence = models.PositiveIntegerField(
        help_text="1 for the first checkpoint on this wallet, and no gaps after it."
    )
    balance = models.DecimalField(
        **MONEY,
        help_text="The settled balance as at this checkpoint: everything archived up to here.",
    )
    credited = models.DecimalField(**MONEY, help_text="Money in, across the entries archived here.")
    debited = models.DecimalField(**MONEY, help_text="Money out, across the entries archived here.")
    entry_count = models.PositiveIntegerField(help_text="How many entries this checkpoint folded.")
    created_at = models.DateTimeField(default=timezone.now, editable=False)

    class Meta:
        ordering = ["-sequence"]
        constraints = [
            models.UniqueConstraint(
                fields=["wallet", "sequence"], name="wallet_checkpoint_sequence_once"
            )
        ]
        indexes = [models.Index(fields=["wallet", "-sequence"])]

    def __str__(self) -> str:
        return f"#{self.sequence} {self.balance}"


class WalletEntryQuerySet(models.QuerySet["WalletEntry"]):
    def counted(self) -> "WalletEntryQuerySet":
        """Everything a balance is made of: settled, and settled-then-reversed.

        Not the same as :meth:`settled` -- see :data:`COUNTED` for why a reversed
        entry is still part of the arithmetic.
        """
        return self.filter(status__in=COUNTED)

    def settled(self) -> "WalletEntryQuerySet":
        """The only entries a balance is made of."""
        return self.filter(status=SETTLED)

    def pending(self) -> "WalletEntryQuerySet":
        return self.filter(status=str(EntryStatus.PENDING))

    def awaiting_approval(self) -> "WalletEntryQuerySet":
        """Movements a person can still actually decide about.

        Both halves matter. ``approval`` alone would include movements that have
        since been cancelled or expired: still marked ``requested``, no longer
        possible to apply or refuse, and so a row that would sit in the back
        office queue for ever with nothing anybody could do to clear it.
        """
        return self.pending().filter(approval=Approval.REQUESTED)

    def unarchived(self) -> "WalletEntryQuerySet":
        """Everything no checkpoint has counted yet."""
        return self.filter(checkpoint__isnull=True)

    def archivable(self) -> "WalletEntryQuerySet":
        """Unarchived entries that can no longer change.

        A pending entry is excluded however old it is: folding it into a sum
        would write down a number that is still free to move.
        """
        return self.unarchived().filter(status__in=TERMINAL)

    def signed_total(self) -> Decimal:
        """Credits minus debits over this queryset, as one query.

        ``Sum`` with a filter rather than two round trips, and ``or ZERO``
        because an empty queryset sums to ``None`` and arithmetic against that
        is a ``TypeError`` at the worst possible moment.
        """
        totals = self.aggregate(
            credit=Sum("amount", filter=Q(direction=str(Direction.CREDIT))),
            debit=Sum("amount", filter=Q(direction=str(Direction.DEBIT))),
        )
        return (totals["credit"] or ZERO) - (totals["debit"] or ZERO)


class WalletEntry(models.Model):
    """One movement of money, and where it has got to.

    Immutable in everything but ``status``: the amount, the direction, the rail
    and the wallet are what happened, and something that happened does not get
    edited. Undoing a settled entry is a *second* entry in the other direction --
    see :func:`apps.wallet.services.WalletService.reverse` -- which is what keeps
    this table reconcilable against the rail that also remembers it.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    wallet = models.ForeignKey(Wallet, on_delete=models.CASCADE, related_name="entries")
    kind = models.CharField(max_length=24, choices=EntryKind.choices)
    direction = models.CharField(
        max_length=8,
        choices=Direction.choices,
        editable=False,
        help_text="Derived from the kind, so the two cannot disagree.",
    )
    method = models.CharField(
        max_length=24, choices=Method.choices, help_text="The rail this movement travelled on."
    )
    payment_method = models.ForeignKey(
        PaymentMethod,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="entries",
        help_text=(
            "The configured method this went through. Empty for the movements no "
            "customer chose: a transfer between wallets here, a fee, an operator's "
            "correction. Protected rather than cascading -- a method that has moved "
            "money is history, and history does not get deleted to tidy a list."
        ),
    )
    network = models.ForeignKey(
        MethodNetwork,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="entries",
        help_text=(
            "For crypto: the chain it travelled on. Which chain is not a detail -- "
            "see MethodNetwork."
        ),
    )
    amount = models.DecimalField(
        **MONEY,
        validators=[MinValueValidator(Decimal("0.0001"))],
        help_text=(
            "What this moves the balance by, in the wallet's currency, after charges "
            "and after conversion. Always positive; direction says which way. This is "
            "the only field a balance is made of."
        ),
    )
    currency = models.CharField(
        max_length=12,
        blank=True,
        help_text=(
            "The currency the customer named the movement in. Blank means the wallet's "
            "own -- the ordinary case, and no conversion happened."
        ),
    )
    gross_amount = models.DecimalField(
        **MONEY,
        default=ZERO,
        help_text=(
            "The figure the customer actually asked for, in `currency`, before any "
            "charge came off it. What they will recognise on a statement."
        ),
    )
    fee_total = models.DecimalField(
        **MONEY,
        default=ZERO,
        help_text=(
            "What the charges came to, in `currency`. Frozen here rather than "
            "recomputed from the fee table, because the fee table changes and what "
            "somebody was charged in March does not."
        ),
    )
    exchange_rate = models.DecimalField(
        **RATE,
        null=True,
        blank=True,
        help_text=(
            "The rate this converted at, spread included. Stored on the entry because "
            "`what did we convert at?` has to be answerable years later, when the rate "
            "row that priced it has long been superseded."
        ),
    )
    destination = models.CharField(
        max_length=255,
        blank=True,
        help_text=(
            "Where a payout went: an address, an IBAN, a masked card. Empty for money coming in."
        ),
    )
    approval = models.CharField(
        max_length=16,
        choices=Approval.choices,
        default=str(Approval.NOT_REQUIRED),
        help_text="Whether a person still has to apply this. A requested movement cannot settle.",
    )
    reviewed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="wallet_reviews",
        help_text="The operator who applied or refused it.",
    )
    reviewed_at = models.DateTimeField(null=True, blank=True)
    review_note = models.CharField(
        max_length=255, blank=True, help_text="Why it was applied, or why it was not."
    )
    status = models.CharField(
        max_length=16,
        choices=EntryStatus.choices,
        default=str(EntryStatus.PENDING),
        help_text="`done` is money, and so is `reversed` -- its counterpart is what undid it.",
    )
    reference = models.CharField(
        max_length=120,
        help_text=(
            "The caller's idempotency key. Unique per wallet, so a retried request "
            "returns the first entry instead of moving the money twice."
        ),
    )
    external_reference = models.CharField(
        max_length=200,
        blank=True,
        help_text="What the rail calls this: a processor charge id, a transaction hash.",
    )
    description = models.CharField(max_length=255, blank=True)
    metadata = models.JSONField(
        default=dict, blank=True, help_text="Whatever the integration needs kept with the entry."
    )
    counterparty = models.ForeignKey(
        "self",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="counterparts",
        help_text=(
            "The other half of a pair: the far side of an internal transfer, or the "
            "entry this one reverses."
        ),
    )
    checkpoint = models.ForeignKey(
        WalletCheckpoint,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="entries",
        help_text=(
            "The checkpoint that has already counted this. Empty means it still counts on its own."
        ),
    )
    created_at = models.DateTimeField(default=timezone.now, editable=False)
    updated_at = models.DateTimeField(auto_now=True)
    settled_at = models.DateTimeField(
        null=True, blank=True, help_text="When it became `done`. Empty until it does."
    )

    objects = WalletEntryQuerySet.as_manager()

    class Meta:
        ordering = ["-created_at", "-id"]
        verbose_name_plural = "wallet entries"
        constraints = [
            models.UniqueConstraint(
                fields=["wallet", "reference"], name="wallet_entry_reference_once_per_wallet"
            ),
            models.CheckConstraint(
                condition=Q(amount__gt=Decimal("0")),
                name="wallet_entry_amount_positive",
                violation_error_message="An entry's amount is positive; direction says which way.",
            ),
        ]
        indexes = [
            models.Index(fields=["wallet", "-created_at"]),
            models.Index(fields=["wallet", "status"]),
            # The archive walks exactly this: one wallet's unarchived entries,
            # oldest first. Without it the scheduled command table-scans nightly.
            models.Index(fields=["wallet", "checkpoint", "created_at"]),
            models.Index(fields=["external_reference"]),
            # The back office queue: everything waiting on a person, oldest first.
            models.Index(fields=["approval", "created_at"]),
            models.Index(fields=["payment_method", "status"]),
        ]

    def __str__(self) -> str:
        return f"{self.get_kind_display()} {self.signed_amount}"

    def save(self, *args: Any, **kwargs: Any) -> None:
        """Derive what can be derived, so no caller can write the two apart.

        ``direction`` follows the kind and ``settled_at`` follows the status. Both
        are set here rather than by each caller, because "the service remembered
        to stamp it" is not an invariant.

        ``settled_at`` is cleared for the statuses that mean *this never
        happened* -- and reversal is not one of them. A reversed entry did
        settle, on a day somebody can be asked about; wiping the timestamp when
        a chargeback lands would destroy the answer to "when did this clear?" at
        exactly the moment it starts being asked. So the test is membership of
        :data:`COUNTED`, not equality with ``DONE``.
        """
        self.direction = direction_of(self.kind)
        if self.status == SETTLED and self.settled_at is None:
            self.settled_at = timezone.now()
        if self.status not in COUNTED:
            self.settled_at = None
        if update_fields := kwargs.get("update_fields"):
            kwargs["update_fields"] = sorted({*update_fields, "direction", "settled_at"})
        super().save(*args, **kwargs)

    @property
    def signed_amount(self) -> Decimal:
        """The amount as it moves the balance: positive in, negative out."""
        return self.amount if self.direction == str(Direction.CREDIT) else -self.amount

    @property
    def is_settled(self) -> bool:
        return self.status == SETTLED

    @property
    def is_terminal(self) -> bool:
        return self.status in TERMINAL

    @property
    def counts_towards_balance(self) -> bool:
        """Whether this is part of the balance. A reversed entry still is."""
        return self.status in COUNTED

    @property
    def movement_currency(self) -> str:
        """The currency the customer named this in, falling back to the wallet's."""
        return self.currency or self.wallet.currency

    @property
    def net_amount(self) -> Decimal:
        """What survived the charges, in the movement's own currency.

        Derived rather than stored, because unlike the gross and the fees it is
        not an independent fact -- it is the subtraction of two that are.
        """
        return money(self.gross_amount - self.fee_total)

    @property
    def converted(self) -> bool:
        """Whether this crossed a currency boundary on its way into the wallet."""
        return bool(self.currency and self.currency != self.wallet.currency)

    @property
    def awaiting_approval(self) -> bool:
        """Whether a person is still the thing standing between this and settling.

        Both halves, and the same two as
        :meth:`WalletEntryQuerySet.awaiting_approval` -- deliberately, because a
        row the queue excludes must not be a row the record describes as waiting.
        A movement the account cancelled keeps its ``requested`` approval for the
        trail, and nobody is waiting on it: it is over.
        """
        return self.approval == str(Approval.REQUESTED) and not self.is_terminal

    @property
    def is_cleared(self) -> bool:
        """Whether a person's permission is no longer in the way of settling."""
        return self.approval in CLEARED

    def can_become(self, status: str) -> bool:
        """Whether this entry is allowed to move to ``status``.

        Two gates, and both have to open. The state machine says whether the
        movement can get there from where it is; approval says whether anyone has
        agreed to let it. Only settling is held by the second -- an unapproved
        movement may still fail, be cancelled or expire, because nothing dangerous
        happens when money does not move.
        """
        if status not in TRANSITIONS.get(self.status, frozenset()):
            return False
        return self.is_cleared if status == SETTLED else True

    def clean(self) -> None:
        """Say what the app's rules say, early enough to be an admin form error."""
        if self.kind not in KIND_DIRECTIONS:
            raise ValidationError({"kind": f"No direction is declared for {self.kind!r}."})
        rail = spec(self.method) if self.method in Method.values else None
        if rail is not None and not rail.carries(direction_of(self.kind)):
            raise ValidationError(
                {"method": f"{rail.label} does not carry money {direction_of(self.kind)}."}
            )
        if self.amount is not None and self.amount <= 0:
            raise ValidationError({"amount": "An amount is positive; the kind says which way."})


class WalletCharge(models.Model):
    """One fee, as it was charged, kept beside the movement it came off.

    A copy rather than a reference, and deliberately so. The fee table is
    configuration: an operator changes the commission on Tuesday and every row in
    it becomes a statement about Wednesday onwards. What a customer was charged on
    Monday is not configuration, it is what happened -- so the percentage, the
    label and the amount are written down here at the moment they are applied, and
    a later edit to the fee cannot rewrite the receipt.

    The link back to :class:`~apps.wallet.catalog.MethodFee` survives as a
    breadcrumb for whoever is asking "which rule did this?", and goes null rather
    than taking the charge with it when the rule is eventually deleted.

    Several per entry, because "commission 2.90, tax 0.58" answers a question that
    "fees 3.48" does not, and the question always gets asked.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    entry = models.ForeignKey("WalletEntry", on_delete=models.CASCADE, related_name="charges")
    kind = models.CharField(max_length=16, choices=ChargeKind.choices)
    label = models.CharField(max_length=120, help_text="What the customer sees on this line.")
    percent = models.DecimalField(
        **PERCENT,
        default=Decimal("0"),
        help_text="The rate applied, as it stood then. Zero for a flat charge.",
    )
    amount = models.DecimalField(
        **MONEY, help_text="What this line came to, in the movement's currency."
    )
    absorbed = models.BooleanField(
        default=False,
        help_text=(
            "This deployment paid it rather than the customer. Recorded so the "
            "reporting adds up; it changed nothing about what they received."
        ),
    )
    fee = models.ForeignKey(
        MethodFee,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="charges",
        help_text="The rule that produced this, while it still exists.",
    )
    position = models.IntegerField(default=0, help_text="The order they were applied in.")
    created_at = models.DateTimeField(default=timezone.now, editable=False)

    class Meta:
        ordering = ["position", "created_at"]
        indexes = [models.Index(fields=["entry", "position"])]

    def __str__(self) -> str:
        return f"{self.label} {self.amount}"

    @property
    def payable(self) -> bool:
        """Whether this one actually came out of the customer's money."""
        return not self.absorbed
