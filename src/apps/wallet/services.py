"""Everything an account can do with its money, decided once for every transport.

The HTTP router, the GraphQL schema and the gRPC service are three doors onto
this file. None of them decides anything: a rule enforced in one door and
forgotten in another is how a wallet ends up with two answers to "may I withdraw
this?", and the wrong one is always the one somebody found.

Three rules run through all of it.

**Nothing is scoped by an argument.** Every call takes the account that made the
request and derives the wallet from it. There is no ``wallet_id`` parameter to
get wrong, so no request can read or move somebody else's money.

**Every write that depends on a balance takes the wallet's row lock first.**
Not because writes are slow, but because a balance read outside a lock is a
number that was true when it was read. Two withdrawals of eight against a
balance of ten both pass a check made on an unlocked read, and the wallet ends
up at minus six. ``select_for_update`` on the wallet serialises them: the second
transaction blocks until the first commits, and then sees two.

**A retry is not a second movement.** Every write takes a ``reference``, unique
per wallet, and a call carrying one that already exists returns the entry that
already exists instead of creating another. That is what makes a client safe to
retry on a timeout, which is the one thing a payment client will certainly do.

Settling and failing are separate operations rather than a hidden effect of
creating, because the confirmation comes from outside this app -- a webhook, a
batch file, an operator. The rail decides when money has moved; this app records
that it did.

Two more things run through the write path, both of them consequences of the
configuration living in the database rather than in code.

**A movement is priced before it is written, by the same function that quotes
it.** :func:`apps.wallet.charges.quote_movement` works out the commission, the
tax, the network fee and the exchange rate, and the recording path takes its
answer rather than recomputing one. A customer who was quoted a cost and then
charged a different one has found a bug, and the only reliable way not to have
that bug is to have one implementation.

**A movement through a method that requires approval is a request.** It is
written down, it is visible, it holds the money it claims -- and it cannot settle
until an operator applies it. That is the default for a new method, because the
alternative default is money moving on an unverified claim. See
:meth:`WalletService.approve`.

The one thing that is never priced is a transfer between two wallets in this
app. The money does not leave, so there is no cost to pass on, and this app does
not invent one.
"""

from decimal import Decimal
from typing import Any
from uuid import UUID

from django.conf import settings
from django.db import IntegrityError, transaction
from django.utils import timezone

from apps.wallet.balances import Balance, balance_of
from apps.wallet.catalog import Applies, ExchangeRate, PaymentMethod
from apps.wallet.charges import Quote, convert, free_quote, quote_movement
from apps.wallet.errors import (
    ApprovalRequired,
    CurrencyNotAllowed,
    InsufficientFunds,
    InvalidAmount,
    InvalidDestination,
    InvalidTransition,
    MethodNotAllowed,
    NetworkRequired,
    NoExchangeRate,
    ReferenceReused,
    WalletError,
    WalletFrozen,
    WalletNotFound,
)
from apps.wallet.methods import Direction, Method, enabled_methods
from apps.wallet.models import (
    Approval,
    EntryKind,
    EntryStatus,
    Wallet,
    WalletCharge,
    WalletEntry,
    direction_of,
)
from apps.wallet.money import ZERO, money

#: What a deployment gets when it has said nothing. Both are overridable, and
#: the settings that override them are read per call rather than captured here:
#: a module constant frozen at import is a setting a deployment can set, a
#: settings check can validate, and nothing can actually change.
MAX_PAGE = 200
DEFAULT_PAGE = 50


def page_size(limit: int | None) -> int:
    """How many rows one listing returns, held inside the deployment's ceiling.

    Public because a transport has to echo back the page it actually served: a
    client that paged on the number it sent would step over the rows the ceiling
    clamped away.
    """
    asked = limit or int(getattr(settings, "WALLET_PAGE_SIZE", DEFAULT_PAGE))
    ceiling = int(getattr(settings, "WALLET_MAX_PAGE_SIZE", MAX_PAGE))
    return max(1, min(asked, ceiling))


#: The kinds a client may ask for directly. Everything else -- a fee, a
#: chargeback, an adjustment -- is written by the code or the operator that has
#: a reason to, never by the account whose balance it moves.
CLIENT_KINDS = frozenset({str(EntryKind.DEPOSIT), str(EntryKind.WITHDRAWAL)})


#: Re-exported so a transport can `from apps.wallet.services import WalletError`
#: and catch every refusal in one clause. They live in `apps.wallet.errors`
#: because the pricing engine raises them too, and it must not import a service.
__all__ = [
    "ApprovalRequired",
    "CurrencyNotAllowed",
    "InsufficientFunds",
    "InvalidAmount",
    "InvalidDestination",
    "InvalidTransition",
    "MethodNotAllowed",
    "NetworkRequired",
    "NoExchangeRate",
    "WalletError",
    "WalletFrozen",
    "WalletNotFound",
    "WalletService",
    "entry_payload",
    "page_size",
    "method_payload",
    "wallet_service",
]


def _limit(name: str, fallback: str) -> Decimal:
    return Decimal(str(getattr(settings, name, fallback)))


class WalletService:
    """The wallet, the entries, and the six things that move them."""

    # -- the wallet itself ------------------------------------------------

    def wallet_for(self, user: Any, *, create: bool | None = None) -> Wallet:
        """This account's wallet, opening one the first time if the deployment says so.

        ``DJANGO_WALLET_AUTO_CREATE`` is on by default, because "every account
        has a wallet" is the promise this app makes and a project that has to
        remember to open one will forget for exactly one user. A deployment that
        wants wallets to be granted deliberately turns it off, and then a request
        from an account without one is a 404 rather than a silent opening.
        """
        wallet = Wallet.objects.filter(user=user).first()
        if wallet is not None:
            return wallet
        if create is None:
            create = bool(getattr(settings, "WALLET_AUTO_CREATE", True))
        if not create:
            raise WalletNotFound("This account has no wallet.")
        return self.open(user)

    def open(self, user: Any, *, currency: str | None = None) -> Wallet:
        """Open a wallet for an account. Safe to race: the second caller gets the first's.

        The one-to-one constraint is the arbiter rather than a check-then-create,
        which two requests arriving together would both pass.
        """
        try:
            with transaction.atomic():
                return Wallet.objects.create(
                    user=user, currency=(currency or settings.WALLET_CURRENCY).upper()
                )
        except IntegrityError:
            existing = Wallet.objects.filter(user=user).first()
            if existing is None:  # pragma: no cover - only if something else failed
                raise
            return existing

    def balance(self, user: Any) -> dict[str, Any]:
        """Both numbers: what is there, and what is on its way.

        Never one number. A client shown a single figure has to guess whether it
        may be spent, and it will guess the friendly way.
        """
        return balance_of(self.wallet_for(user)).payload()

    def summary(self, user: Any) -> dict[str, Any]:
        """The balance plus the wallet's own state, which is what a wallet screen renders."""
        wallet = self.wallet_for(user)
        balance: Balance = balance_of(wallet)
        return {
            "id": wallet.pk,
            "currency": wallet.currency,
            "status": wallet.status,
            "created_at": wallet.created_at.isoformat(),
            "balance": balance.payload(),
        }

    # -- reading the history ----------------------------------------------

    def _entries(
        self,
        user: Any,
        *,
        kind: str | None,
        status: str | None,
        method: str | None,
        direction: str | None,
    ) -> Any:
        entries = (
            self.wallet_for(user)
            .entries.select_related("wallet", "payment_method", "network")
            .prefetch_related("charges")
        )
        for field, value in (
            ("kind", kind),
            ("status", status),
            ("method", method),
            ("direction", direction),
        ):
            if value:
                entries = entries.filter(**{field: value})
        return entries

    def entries(
        self,
        user: Any,
        *,
        kind: str | None = None,
        status: str | None = None,
        method: str | None = None,
        direction: str | None = None,
        limit: int | None = None,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        """Newest first, every movement this wallet has ever had, whatever became of it.

        Failed and cancelled entries are included rather than hidden: a customer
        asking why their deposit did not arrive is asking about exactly the rows
        an app that only listed successes would have dropped.
        """
        page = page_size(limit)
        start = max(0, offset)
        rows = self._entries(user, kind=kind, status=status, method=method, direction=direction)
        return [entry_payload(entry) for entry in rows[start : start + page]]

    def count(
        self,
        user: Any,
        *,
        kind: str | None = None,
        status: str | None = None,
        method: str | None = None,
        direction: str | None = None,
    ) -> int:
        """How many rows the same filters match, so a client can page without fetching them."""
        return self._entries(
            user, kind=kind, status=status, method=method, direction=direction
        ).count()

    def entry(self, user: Any, entry_id: UUID) -> dict[str, Any]:
        """One movement out of this account's own wallet."""
        return entry_payload(self._resolve(user, entry_id))

    def checkpoints(self, user: Any, *, limit: int | None = None) -> list[dict[str, Any]]:
        """The archive: each folded run of entries and the balance it left.

        Published rather than kept internal, because it is the audit trail that
        makes a derived balance checkable -- a reader can add the checkpoints up
        and get the same number the app reports.
        """
        page = page_size(limit)
        return [
            {
                "id": checkpoint.pk,
                "sequence": checkpoint.sequence,
                "balance": checkpoint.balance,
                "credited": checkpoint.credited,
                "debited": checkpoint.debited,
                "entry_count": checkpoint.entry_count,
                "created_at": checkpoint.created_at.isoformat(),
            }
            for checkpoint in self.wallet_for(user).checkpoints.all()[:page]
        ]

    # -- what this deployment offers --------------------------------------

    def _methods(self) -> Any:
        """Every configured method a customer could actually use, loaded whole.

        Both gates -- the row's own switch and whether the deployment runs its
        rail -- and everything the payload renders, so listing twenty methods is
        a handful of queries rather than a hundred.
        """
        return PaymentMethod.objects.usable().prefetch_related(
            "currencies__networks", "fees__currency"
        )

    def methods(
        self, direction: str | None = None, *, currency: str | None = None
    ) -> list[dict[str, Any]]:
        """The ways money can move here, priced, with their limits and currencies.

        The endpoint a client renders its payment screen from. A client that
        hard-codes this list is a client that breaks the afternoon an operator
        turns a method on, so nothing about a method is knowable except from here.

        A method with no enabled currency is left out rather than published as an
        unusable choice: it is a row somebody started configuring and has not
        finished, and offering it only produces a refusal at the last step.
        """
        found = self._methods()
        if direction:
            found = found.for_direction(direction)
        published = []
        for method in found:
            if direction and not method.carries(direction):
                continue
            payload = method_payload(method, direction=direction, currency=currency)
            if not payload["currencies"]:
                continue
            published.append(payload)
        return published

    def method(self, code: str, *, direction: str | None = None) -> dict[str, Any]:
        """One method in full: its currencies, its chains, and what it charges."""
        found = self._methods().filter(code=code).first()
        if found is None:
            raise MethodNotAllowed(f"No method called {code!r} is available here.")
        return method_payload(found, direction=direction)

    def quote(
        self,
        user: Any,
        *,
        method: str,
        direction: str,
        amount: Decimal,
        currency: str = "",
        network: str = "",
    ) -> dict[str, Any]:
        """What a movement would cost and produce, without writing anything.

        The same call the recording path makes, with the writing left out -- so a
        customer shown "you will receive 97.10" receives 97.10. Every limit is
        checked here too, which makes this the cheap way for a client to find out
        that an amount is out of bounds before asking somebody to confirm it.
        """
        wallet = self.wallet_for(user)
        configured = self._method(method, direction)
        return quote_movement(
            configured,
            direction=direction,
            amount=Decimal(amount),
            currency=(currency or wallet.currency),
            wallet_currency=wallet.currency,
            network_code=network,
        ).payload()

    def rates(self, *, base: str | None = None, quote: str | None = None) -> list[dict[str, Any]]:
        """The conversion rates in force, as a customer would be given them.

        The margin is published rather than hidden inside the rate. A deployment
        keeping a spread is entitled to keep one; a deployment that will not say
        it is keeping one is a different thing, and this app does not help with
        that.
        """
        live = ExchangeRate.objects.live()
        if base:
            live = live.filter(base=base.upper())
        if quote:
            live = live.filter(quote=quote.upper())
        seen: dict[tuple[str, str], dict[str, Any]] = {}
        for rate in live:
            # Ordered newest first, so the first of each pair is the live one and
            # the superseded rows behind it are history rather than choices.
            seen.setdefault(
                (rate.base, rate.quote),
                {
                    "base": rate.base,
                    "quote": rate.quote,
                    "rate": rate.rate,
                    "margin_percent": rate.margin_percent,
                    "source": rate.source,
                    "effective_from": rate.effective_from.isoformat(),
                },
            )
        return list(seen.values())

    def exchange(
        self, *, amount: Decimal, base: str, quote: str, direction: str = str(Direction.CREDIT)
    ) -> dict[str, Any]:
        """Convert an amount between two currencies at the live rate, spread included.

        A calculator, not a movement: nothing is written and no wallet is touched.
        ``direction`` says which side of the spread to quote -- what a customer
        would receive, or what they would have to pay.
        """
        return convert(Decimal(amount), base, quote, direction=direction).payload()

    # -- moving money -----------------------------------------------------

    def deposit(
        self,
        user: Any,
        *,
        amount: Decimal,
        method: str,
        reference: str,
        currency: str = "",
        network: str = "",
        external_reference: str = "",
        description: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Record money arriving, priced, and in whatever state the method leaves it.

        ``amount`` is what the customer is paying *in*, in ``currency`` -- the
        figure they typed, before anything came off it. What reaches the wallet is
        that less the charges, converted into the wallet's own currency, and it is
        :func:`apps.wallet.charges.quote_movement` that works out both.

        Where it ends up depends on the method, and the two questions are separate:
        a cash deposit settles at once while a card waits on the processor, and
        either of them may *also* be waiting on an operator to apply it. A method
        that requires approval produces a request, whatever its rail does.
        """
        return self._record(
            user,
            kind=str(EntryKind.DEPOSIT),
            amount=amount,
            method=method,
            reference=reference,
            currency=currency,
            network=network,
            external_reference=external_reference,
            description=description,
            metadata=metadata,
        )

    def withdraw(
        self,
        user: Any,
        *,
        amount: Decimal,
        method: str,
        reference: str,
        currency: str = "",
        network: str = "",
        destination: str = "",
        external_reference: str = "",
        description: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Record money leaving, after checking -- under the lock -- that it is there.

        ``amount`` is what leaves the wallet; the charges come out of it, so the
        customer receives the remainder. The same rule as a deposit, read from the
        other end, and it means a limit is about the figure the customer named
        rather than about some net figure they never saw.

        The balance check is against ``available``, not ``settled``: money behind
        a withdrawal that has not left yet is already spoken for, and letting it be
        promised twice means choosing later which payout to fail.

        A payout needs somewhere to go. What counts as somewhere depends on the
        rail -- an address on the right chain, an account number -- and a crypto
        payout to an address that does not match the chain's shape is refused
        here, because the network will not refuse it: it will deliver the money to
        nobody.
        """
        return self._record(
            user,
            kind=str(EntryKind.WITHDRAWAL),
            amount=amount,
            method=method,
            reference=reference,
            currency=currency,
            network=network,
            destination=destination,
            external_reference=external_reference,
            description=description,
            metadata=metadata,
        )

    def transfer(
        self,
        user: Any,
        *,
        to_user: Any,
        amount: Decimal,
        reference: str,
        description: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Move money between two wallets in this app, as one indivisible pair.

        **Free, and not by default -- by construction.** No commission, no tax, no
        fixed cost, no spread, and no way for an administrator to add one: the
        money never leaves this app, so nothing was spent moving it and there is
        nothing to pass on. :meth:`MethodFee.clean` refuses to store a fee against
        the internal rail at all, so this is not a zero that a configuration
        change can quietly make non-zero. The amount that leaves one wallet is the
        amount that arrives in the other, always.

        Both entries are written in one transaction and settle immediately: there
        is no rail in the middle to wait for, and so nothing to approve either.
        Both wallets are locked, and always in the same order -- by primary key --
        because two transfers crossing in opposite directions would otherwise take
        the two locks in opposite orders and deadlock.
        """
        if getattr(to_user, "pk", None) == getattr(user, "pk", None):
            raise WalletError("A transfer needs two different accounts.")

        sender = self.wallet_for(user)
        recipient = self.wallet_for(to_user)
        first, second = sorted([sender.pk, recipient.pk], key=str)

        with transaction.atomic():
            locked = {
                wallet.pk: wallet
                for wallet in (
                    Wallet.objects.select_for_update().get(pk=first),
                    Wallet.objects.select_for_update().get(pk=second),
                )
            }
            sender, recipient = locked[sender.pk], locked[recipient.pk]
            if existing := self._existing(sender, reference):
                self._check_same_request(
                    existing, kind=str(EntryKind.TRANSFER_OUT), amount=money(amount), method=""
                )
                return entry_payload(existing)
            if sender.currency != recipient.currency:
                raise WalletError(
                    "Both wallets have to be in the same currency; this app does not convert."
                )
            self._check_amount(amount, str(Direction.DEBIT))
            self._check_wallet(sender, str(Direction.DEBIT))
            self._check_wallet(recipient, str(Direction.CREDIT))
            self._check_funds(sender, amount)

            moving = money(amount)
            free = free_quote(
                direction=str(Direction.DEBIT),
                amount=moving,
                currency=sender.currency,
                rail=str(Method.INTERNAL),
            )
            out = WalletEntry.objects.create(
                wallet=sender,
                kind=str(EntryKind.TRANSFER_OUT),
                method=str(Method.INTERNAL),
                amount=free.wallet_amount,
                gross_amount=free.gross,
                fee_total=free.fee_total,
                status=str(EntryStatus.DONE),
                approval=str(Approval.NOT_REQUIRED),
                reference=reference,
                description=description,
                metadata=metadata or {},
            )
            incoming = WalletEntry.objects.create(
                wallet=recipient,
                kind=str(EntryKind.TRANSFER_IN),
                method=str(Method.INTERNAL),
                # The same figure, because nothing was taken out of it in transit.
                amount=free.wallet_amount,
                gross_amount=free.gross,
                fee_total=free.fee_total,
                status=str(EntryStatus.DONE),
                approval=str(Approval.NOT_REQUIRED),
                # Namespaced, because the reference is unique per wallet and the
                # recipient may well have used the sender's string for something
                # of its own.
                reference=f"transfer:{out.pk}",
                description=description,
                metadata=metadata or {},
                counterparty=out,
            )
            out.counterparty = incoming
            out.save(update_fields=["counterparty"])
            return entry_payload(out)

    # -- moving an entry through its states -------------------------------

    def settle(self, user: Any, entry_id: UUID, *, external_reference: str = "") -> dict[str, Any]:
        """Confirm that a pending movement really happened. This is where money appears.

        A settling withdrawal is re-checked against the balance under the lock:
        between recording and confirming, a chargeback may have taken the money
        away, and paying it out anyway is the one mistake a wallet cannot undo.
        """
        return self._transition(
            user, entry_id, str(EntryStatus.DONE), external_reference=external_reference
        )

    def fail(self, user: Any, entry_id: UUID, *, reason: str = "") -> dict[str, Any]:
        """The rail refused it. Terminal, and it never counted for anything."""
        return self._transition(user, entry_id, str(EntryStatus.FAILED), reason=reason)

    def cancel(self, user: Any, entry_id: UUID, *, reason: str = "") -> dict[str, Any]:
        """Withdraw a movement before it settles. Only ever a pending entry."""
        return self._transition(user, entry_id, str(EntryStatus.CANCELLED), reason=reason)

    def expire(self, user: Any, entry_id: UUID) -> dict[str, Any]:
        """Give up on a pending movement nothing ever confirmed."""
        return self._transition(user, entry_id, str(EntryStatus.EXPIRED))

    # -- the back office --------------------------------------------------

    def awaiting_approval(
        self, *, limit: int | None = None, offset: int = 0, method: str | None = None
    ) -> list[dict[str, Any]]:
        """Every movement waiting on a person, oldest first.

        The queue. Oldest first rather than newest, because the cost of this
        queue is somebody's deposit sitting unapplied, and a stack would leave the
        oldest one at the bottom forever.

        Only movements still open: one that has since been cancelled or expired
        keeps its ``requested`` approval for the record, and is no longer
        something anybody can act on -- so it is not in the queue, where it would
        sit unclearable.

        Not scoped to a caller: this is the one reading in the app that crosses
        wallets, and the transport publishing it is responsible for letting only
        an operator through.
        """
        page = page_size(limit)
        start = max(0, offset)
        rows = (
            WalletEntry.objects.awaiting_approval()
            .select_related("wallet", "payment_method", "network")
            .prefetch_related("charges")
            .order_by("created_at")
        )
        if method:
            rows = rows.filter(payment_method__code=method)
        return [entry_payload(entry) for entry in rows[start : start + page]]

    def approve(self, entry_id: UUID, *, by: Any = None, note: str = "") -> dict[str, Any]:
        """Apply a requested movement, and settle it if there is nothing left to wait for.

        What an operator does in the back office when they have seen the money
        arrive, or decided the payout should go.

        A method whose rail settles immediately -- cash over a counter -- settles
        here, because the approval *was* the confirmation. Anything else stays
        pending until its rail says the money moved.

        **Approving and settling are one transaction, and it is all or nothing.**
        If the settlement is refused -- a payout whose money went while the
        request sat in the queue -- the approval goes back with it and the
        movement stays a request. That is deliberate, and it is the safer of the
        two designs: the alternative leaves a payout marked *approved* but
        unsettled, which is a standing authorisation that would fire the moment
        the balance recovered, with nobody looking at it a second time. An
        operator who sees the refusal can decide again once they know why.

        Idempotent: approving an approved movement returns it. Webhooks and
        double-clicked buttons both happen.
        """
        with transaction.atomic():
            entry = self._locked_across_wallets(entry_id)
            if entry.approval == str(Approval.APPROVED):
                return entry_payload(entry)
            if entry.approval != str(Approval.REQUESTED):
                raise InvalidTransition(
                    f"This movement is {entry.get_approval_display().lower()}, so there is "
                    "nothing to apply."
                )
            self._still_open(entry, "apply")
            entry.approval = str(Approval.APPROVED)
            entry.reviewed_by = by if getattr(by, "pk", None) else None
            entry.reviewed_at = timezone.now()
            entry.review_note = note
            entry.save(update_fields=["approval", "reviewed_by", "reviewed_at", "review_note"])

            rail = entry.payment_method.spec if entry.payment_method_id else None
            if (
                rail is not None
                and rail.settles_immediately
                and entry.can_become(str(EntryStatus.DONE))
            ):
                return self._settle_locked(entry)
            return entry_payload(entry)

    def confirm_from_rail(
        self,
        entry_id: UUID,
        *,
        method_code: str,
        event: str,
        external_reference: str = "",
        reason: str = "",
    ) -> dict[str, Any]:
        """What a payment rail is allowed to say about a movement, and nothing else.

        The back-office counterpart of :meth:`settle` and :meth:`fail`, without a
        caller to scope it to: a processor confirms movements belonging to
        whichever account made them, which is the whole point of a webhook.
        Whether the confirmation is genuine is decided before this is reached --
        see :mod:`apps.wallet.hooks` -- and this takes the signature's word for
        it, so it must never be published anywhere the signature is not checked.

        A rail may only speak about **its own** movements. The method whose
        secret signed the request is checked against the method the entry was
        made through, so a processor whose secret leaks cannot settle a movement
        on another company's rail. Defence in depth: it costs one comparison and
        it turns one leaked secret into a smaller problem than it would otherwise
        be.

        Two events, because there are only two things a rail knows: the money
        moved, or it did not. Approval is not among them -- a movement waiting on
        an operator stays waiting however loudly its processor confirms it, which
        is exactly what :meth:`settle` already refuses and this inherits.
        """
        if event not in (str(EntryStatus.DONE), str(EntryStatus.FAILED)):
            raise WalletError(
                f"A rail may only report that a movement settled or failed; {event!r} is neither."
            )
        with transaction.atomic():
            entry = self._locked_across_wallets(entry_id)
            through = entry.payment_method.code if entry.payment_method_id else ""
            if through != method_code:
                # Deliberately the same answer as an entry that does not exist:
                # a rail probing for other processors' entry ids should not be
                # able to tell "not yours" from "no such thing".
                raise WalletNotFound("No such entry.")
            if entry.status == event:
                return entry_payload(entry)
            if event == str(EntryStatus.DONE):
                if not entry.is_cleared:
                    raise ApprovalRequired(
                        "This movement is waiting to be applied by an operator, so it "
                        "cannot settle yet."
                        if entry.awaiting_approval
                        else "This movement was refused, so it cannot settle."
                    )
                if not entry.can_become(event):
                    raise InvalidTransition(
                        f"A {entry.get_status_display().lower()} entry cannot become settled."
                    )
                return self._settle_locked(entry, external_reference=external_reference)
            if not entry.can_become(event):
                raise InvalidTransition(
                    f"A {entry.get_status_display().lower()} entry cannot become failed."
                )
            entry.status = event
            fields = ["status"]
            if external_reference:
                entry.external_reference = external_reference
                fields.append("external_reference")
            if reason:
                entry.metadata = {**entry.metadata, "reason": reason}
                fields.append("metadata")
            entry.save(update_fields=fields)
            return entry_payload(entry)

    def reject(self, entry_id: UUID, *, by: Any = None, note: str = "") -> dict[str, Any]:
        """Refuse a requested movement, and cancel it with the same stroke.

        One operation rather than two, because a refused request that stays
        pending is a row still holding money out of an account's available
        balance -- which is exactly what refusing it was meant to release.
        """
        with transaction.atomic():
            entry = self._locked_across_wallets(entry_id)
            if entry.approval == str(Approval.REJECTED):
                return entry_payload(entry)
            if entry.approval != str(Approval.REQUESTED):
                raise InvalidTransition(
                    f"This movement is {entry.get_approval_display().lower()}, so there is "
                    "nothing to refuse."
                )
            self._still_open(entry, "refuse")
            entry.approval = str(Approval.REJECTED)
            entry.reviewed_by = by if getattr(by, "pk", None) else None
            entry.reviewed_at = timezone.now()
            entry.review_note = note
            fields = ["approval", "reviewed_by", "reviewed_at", "review_note"]
            if entry.status == str(EntryStatus.PENDING):
                entry.status = str(EntryStatus.CANCELLED)
                fields.append("status")
            entry.save(update_fields=fields)
            return entry_payload(entry)

    def reverse(
        self, user: Any, entry_id: UUID, *, reference: str, reason: str = ""
    ) -> dict[str, Any]:
        """Undo a settled movement -- as a second entry, never as an edit.

        A chargeback, a returned transfer, a refund of a payment. The original
        stays exactly as it was and is marked ``reversed``; the money moves back
        on a new entry pointing at it. A ledger that rewrote the original could
        not be reconciled against the rail that still remembers it happening.
        """
        wallet = self.wallet_for(user)
        with transaction.atomic():
            locked = Wallet.objects.select_for_update().get(pk=wallet.pk)
            if existing := self._existing(locked, reference):
                # Same question as everywhere else, asked about the only thing
                # that identifies a reversal: which movement it undoes. A
                # reference reused against a second entry would hand back the
                # first correction and leave the second movement standing.
                if existing.metadata.get("reversal_of") != str(entry_id):
                    raise ReferenceReused(
                        f"The reference {reference!r} was already used to reverse a "
                        "different movement on this wallet."
                    )
                return entry_payload(existing)
            original = self._locked_entry(locked, entry_id)
            if not original.can_become(str(EntryStatus.REVERSED)):
                raise InvalidTransition(
                    f"A {original.get_status_display().lower()} entry cannot be reversed."
                )
            opposite = (
                str(EntryKind.CHARGEBACK)
                if original.direction == str(Direction.CREDIT)
                else str(EntryKind.REFUND)
            )
            correction = WalletEntry.objects.create(
                wallet=locked,
                kind=opposite,
                method=original.method,
                # The rail and the chain are carried across so the record says
                # what the money went back on, rather than leaving a correction
                # that appears to have arrived from nowhere.
                payment_method=original.payment_method,
                network=original.network,
                amount=original.amount,
                # In the wallet's own currency, which is what `amount` already
                # is -- a reversal moves the balance back by exactly what it
                # moved, at the rate that applied then, not at today's.
                gross_amount=original.amount,
                # Nothing. The charges on the original were spent moving money
                # that really did move; a processor does not return its
                # commission because the payment was later disputed. A
                # deployment that does refund a fee writes that as its own
                # entry, where it can be seen and argued with.
                fee_total=ZERO,
                status=str(EntryStatus.DONE),
                approval=str(Approval.NOT_REQUIRED),
                reference=reference,
                external_reference=original.external_reference,
                description=reason or f"Reversal of {original.pk}",
                metadata={"reversal_of": str(original.pk), "reason": reason},
                counterparty=original,
            )
            original.status = str(EntryStatus.REVERSED)
            original.metadata = {**original.metadata, "reversed_by": str(correction.pk)}
            original.save(update_fields=["status", "metadata"])
            return entry_payload(correction)

    # -- the parts every write shares -------------------------------------

    def _still_open(self, entry: WalletEntry, verb: str) -> None:
        """Refuse to record a decision about a movement that is already over.

        Approval and status are independent axes, which means a movement can
        reach a terminal status -- the account cancelled it, the window expired --
        while its approval is still ``requested``. Nothing then stops an operator
        applying it, and while no money moves (the state machine will not settle
        a cancelled entry), the record ends up saying somebody approved a payment
        that had already been withdrawn. An audit trail that can be made to say
        that is not one.
        """
        if entry.is_terminal:
            raise InvalidTransition(
                f"This movement is already {entry.get_status_display().lower()}, so there is "
                f"nothing to {verb}."
            )

    def _existing(self, wallet: Wallet, reference: str) -> WalletEntry | None:
        """The entry a previous call with this reference already wrote, if any."""
        return wallet.entries.filter(reference=reference).first()

    def _check_same_request(
        self, existing: WalletEntry, *, kind: str, amount: Decimal, method: str
    ) -> None:
        """Refuse a reference that has been reused to mean something else.

        The reference makes a retry safe, and it can only do that if this app
        checks that the second call *is* the retry. Without this, a client that
        reuses references -- a counter reset, a fixed string somebody left in --
        asks to deposit five thousand, is answered 200, and is credited with the
        five it deposited yesterday. The refusal is loud because the alternative
        is silent and wrong.

        Compared against the entry's own columns rather than a stored digest of
        the request: the columns are what the money actually did, so this cannot
        drift away from the thing it is meant to be protecting.
        """
        differs = (
            existing.kind != kind
            or money(existing.amount) != money(amount)
            or (existing.payment_method.code if existing.payment_method_id else "") != method
        )
        if differs:
            raise ReferenceReused(
                f"The reference {existing.reference!r} was already used for a different "
                "movement on this wallet. Use a new one -- a reference may only ever "
                "mean one thing."
            )

    def _method(self, code: str, direction: str) -> PaymentMethod:
        """The configured method behind a code, or a refusal that says why not.

        Three different "no"s, kept apart because they send the caller somewhere
        different: there is no such method, there is one but this deployment does
        not run it, and there is one but it does not go that way.
        """
        configured = PaymentMethod.objects.filter(code=code).first()
        if configured is None:
            raise MethodNotAllowed(f"No method called {code!r} exists here.")
        if not configured.is_enabled or configured.rail not in enabled_methods():
            raise MethodNotAllowed(f"{configured.name} is not available at the moment.")
        if not configured.carries(direction):
            way = "take money in" if direction == str(Direction.CREDIT) else "pay money out"
            raise MethodNotAllowed(f"{configured.name} does not {way}.")
        return configured

    def _check_amount(self, amount: Decimal, direction: str) -> None:
        """The deployment-wide limits, checked in the wallet's own currency.

        The outer bound, and a different question from the method's own limits:
        those are about what a processor will accept, this is about how much this
        deployment is willing to move in one go whatever the processor thinks. A
        compromised account emptying a wallet in one call is the thing the
        withdrawal ceiling is for.
        """
        if amount is None or amount <= ZERO:
            raise InvalidAmount("An amount has to be more than zero.")
        if direction == str(Direction.CREDIT):
            smallest, largest = _limit("WALLET_MIN_DEPOSIT", "0"), _limit("WALLET_MAX_DEPOSIT", "0")
        else:
            smallest = _limit("WALLET_MIN_WITHDRAWAL", "0")
            largest = _limit("WALLET_MAX_WITHDRAWAL", "0")
        if smallest and amount < smallest:
            raise InvalidAmount(f"The smallest allowed here is {smallest}.")
        # Zero means no ceiling, which is what a deployment that has not thought
        # about one should get -- rather than a limit this app invented.
        if largest and amount > largest:
            raise InvalidAmount(f"The largest allowed here is {largest}.")

    def _check_destination(self, priced: Quote, direction: str, destination: str) -> None:
        """Where a payout is going, checked before it is promised rather than after.

        The address check is the one that matters, and it is worth being blunt
        about why: a crypto payout to a well-formed address on the *wrong chain*
        is not a failure. The network accepts it, the transaction confirms, and
        the money is gone to an address nobody holds a key for. There is no
        support process that recovers it, so the pattern on the chain is checked
        here and a payout that does not match it is refused.
        """
        if direction != str(Direction.DEBIT):
            return
        method = priced.method
        if method is not None and method.spec.needs_destination and not destination:
            raise InvalidDestination(f"A payout by {method.name} needs somewhere to go.")
        network = priced.network
        if network is not None and destination and not network.accepts_address(destination):
            raise InvalidDestination(
                f"That is not a {network.name} address. Paying an address on the wrong "
                "chain loses the money, so it is refused here."
            )

    def _check_wallet(self, wallet: Wallet, direction: str) -> None:
        if direction == str(Direction.DEBIT) and not wallet.accepts_debit:
            raise WalletFrozen(f"A {wallet.get_status_display().lower()} wallet cannot pay out.")
        if direction == str(Direction.CREDIT) and not wallet.accepts_credit:
            raise WalletFrozen("A closed wallet cannot take money in.")

    def _check_funds(
        self, wallet: Wallet, amount: Decimal, *, already_held: Decimal = ZERO
    ) -> None:
        """The balance check, always inside the wallet's row lock.

        Against ``available`` rather than ``settled``, so pending payouts hold
        their own money, and with an overdraft allowance a deployment can raise
        if it knowingly runs credit.

        ``already_held`` is what *this* movement has itself taken out of
        ``available`` and must not be charged for twice. A payout being recorded
        holds nothing yet, so the default is right for it. A pending payout being
        settled is already inside ``outgoing``, and checking it against an
        ``available`` it is itself depressing is how a customer with exactly
        enough money is told there is none: withdraw the whole balance, and the
        settlement that should pay it out is refused for the amount it is
        already holding.
        """
        allowance = _limit("WALLET_OVERDRAFT_LIMIT", "0")
        available = balance_of(wallet).available
        spendable = available + already_held
        if amount > spendable + allowance:
            raise InsufficientFunds(
                f"Not enough available: {spendable} {wallet.currency}, asked for {amount}."
            )

    def _record(
        self,
        user: Any,
        *,
        kind: str,
        amount: Decimal,
        method: str,
        reference: str,
        currency: str = "",
        network: str = "",
        destination: str = "",
        external_reference: str = "",
        description: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Write one movement, priced, under the lock, once per reference.

        The single path every client-facing write goes down. In order: find the
        method, price the whole movement, check where a payout is going, check the
        deployment's own limits, take the lock, and only then decide whether the
        money is there and write the row.

        The pricing happens *outside* the lock deliberately. It reads
        configuration and does arithmetic -- it touches no wallet and can block
        nobody -- and doing it inside would hold a row lock across queries that
        have nothing to do with that row. What is inside the lock is the part that
        has to be: the idempotency check, the balance check, and the write.
        """
        if not reference:
            raise WalletError("A reference is required; it is what makes a retry safe.")
        direction = direction_of(kind)
        configured = self._method(method, direction)
        wallet = self.wallet_for(user)

        priced = quote_movement(
            configured,
            direction=direction,
            amount=Decimal(amount),
            currency=(currency or wallet.currency),
            wallet_currency=wallet.currency,
            network_code=network,
        )
        self._check_destination(priced, direction, destination)
        self._check_amount(priced.wallet_amount, direction)

        with transaction.atomic():
            locked = Wallet.objects.select_for_update().get(pk=wallet.pk)
            if existing := self._existing(locked, reference):
                self._check_same_request(
                    existing,
                    kind=kind,
                    amount=priced.wallet_amount,
                    method=configured.code,
                )
                return entry_payload(existing)
            self._check_wallet(locked, direction)
            if direction == str(Direction.DEBIT):
                self._check_funds(locked, priced.wallet_amount)

            # Both questions, and they are independent. The rail decides whether
            # the money has arrived; the method's configuration decides whether a
            # person has agreed to it. A movement that needs a person stays
            # pending however fast its rail is.
            needs_person = priced.requires_approval
            entry = WalletEntry.objects.create(
                wallet=locked,
                kind=kind,
                method=configured.rail,
                payment_method=configured,
                network=priced.network,
                amount=priced.wallet_amount,
                # Stored only when it says something the wallet's own currency
                # does not, so the ordinary same-currency movement stays blank.
                currency="" if priced.currency == locked.currency else priced.currency,
                gross_amount=priced.gross,
                fee_total=priced.fee_total,
                exchange_rate=priced.conversion.effective_rate if priced.converted else None,
                destination=destination,
                status=(
                    str(EntryStatus.DONE)
                    if priced.settles_immediately and not needs_person
                    else str(EntryStatus.PENDING)
                ),
                approval=(str(Approval.REQUESTED) if needs_person else str(Approval.NOT_REQUIRED)),
                reference=reference,
                external_reference=external_reference,
                description=description,
                metadata=metadata or {},
            )
            self._write_charges(entry, priced)
            return entry_payload(entry)

    def _write_charges(self, entry: WalletEntry, priced: Quote) -> None:
        """Copy the priced charges onto the entry, in the order they were applied.

        A copy, not a link: see :class:`~apps.wallet.models.WalletCharge`. What
        somebody was charged is a fact about a moment, and the fee table it came
        from is free to change the next morning.
        """
        if not priced.charges:
            return
        WalletCharge.objects.bulk_create(
            [
                WalletCharge(
                    entry=entry,
                    kind=charge.kind,
                    label=charge.label,
                    percent=charge.percent,
                    amount=charge.amount,
                    absorbed=charge.absorbed,
                    fee_id=charge.fee_id,
                    position=position,
                )
                for position, charge in enumerate(priced.charges)
            ]
        )

    def _resolve(self, user: Any, entry_id: UUID) -> WalletEntry:
        entry = (
            self.wallet_for(user)
            .entries.select_related("wallet", "payment_method", "network")
            .prefetch_related("charges")
            .filter(pk=entry_id)
            .first()
        )
        if entry is None:
            raise WalletNotFound("No such entry.")
        return entry

    def _locked_entry(self, wallet: Wallet, entry_id: UUID) -> WalletEntry:
        """The entry, re-read inside the wallet's lock.

        Re-read rather than passed in: whatever the caller read before taking the
        lock may have been settled, failed or reversed by the request it was
        racing.
        """
        entry = (
            WalletEntry.objects.select_related("wallet", "payment_method", "network")
            .filter(wallet=wallet, pk=entry_id)
            .first()
        )
        if entry is None:
            raise WalletNotFound("No such entry.")
        return entry

    def _locked_across_wallets(self, entry_id: UUID) -> WalletEntry:
        """One entry from any wallet, with that wallet's row locked.

        What the back office calls, and the only lookup here that is not scoped to
        the caller -- an operator applies other people's movements, which is the
        job. The wallet is locked before the entry is read, in that order and for
        the usual reason: approving a payout re-checks the balance, and a balance
        read outside the lock is a number that was true a moment ago.
        """
        wallet_id = (
            WalletEntry.objects.filter(pk=entry_id).values_list("wallet_id", flat=True).first()
        )
        if wallet_id is None:
            raise WalletNotFound("No such entry.")
        locked = Wallet.objects.select_for_update().get(pk=wallet_id)
        return self._locked_entry(locked, entry_id)

    def _settle_locked(self, entry: WalletEntry, *, external_reference: str = "") -> dict[str, Any]:
        """Settle an entry whose wallet is already locked by the caller.

        Split out so approval can settle without taking a lock it already holds,
        and so the balance re-check happens in both paths rather than in whichever
        one somebody remembered.
        """
        if entry.direction == str(Direction.DEBIT):
            self._check_funds(entry.wallet, entry.amount, already_held=entry.amount)
        entry.status = str(EntryStatus.DONE)
        fields = ["status"]
        if external_reference:
            entry.external_reference = external_reference
            fields.append("external_reference")
        entry.save(update_fields=fields)
        return entry_payload(entry)

    def _transition(
        self,
        user: Any,
        entry_id: UUID,
        status: str,
        *,
        external_reference: str = "",
        reason: str = "",
    ) -> dict[str, Any]:
        """Move one entry to a new status, if the map allows it.

        Idempotent where it can honestly be: asking for the status an entry is
        already in is success rather than an error, because the webhook that
        delivers a confirmation will deliver it twice.
        """
        wallet = self.wallet_for(user)
        with transaction.atomic():
            locked = Wallet.objects.select_for_update().get(pk=wallet.pk)
            entry = self._locked_entry(locked, entry_id)
            if entry.status == status:
                return entry_payload(entry)
            # Said separately from the state machine, because the answer a caller
            # needs is different: "not yet, a person has to look at it" is not the
            # same problem as "that entry is already finished".
            if status == str(EntryStatus.DONE) and not entry.is_cleared:
                raise ApprovalRequired(
                    "This movement is waiting to be applied by an operator, so it "
                    "cannot settle yet."
                    if entry.awaiting_approval
                    else "This movement was refused, so it cannot settle."
                )
            if not entry.can_become(status):
                raise InvalidTransition(
                    f"A {entry.get_status_display().lower()} entry cannot become "
                    f"{EntryStatus(status).label.lower()}."
                )
            if status == str(EntryStatus.DONE) and entry.direction == str(Direction.DEBIT):
                # Re-checked at settlement, not only at recording: money can have
                # gone between the two, and a payout is not recallable. Its own
                # hold is handed back first -- it is already inside `outgoing`.
                self._check_funds(locked, entry.amount, already_held=entry.amount)

            entry.status = status
            fields = ["status"]
            if external_reference:
                entry.external_reference = external_reference
                fields.append("external_reference")
            if reason:
                entry.metadata = {**entry.metadata, "reason": reason}
                fields.append("metadata")
            entry.save(update_fields=fields)
            return entry_payload(entry)


def charge_payload(charge: Any) -> dict[str, Any]:
    """One fee line, as it was charged."""
    return {
        "kind": charge.kind,
        "label": charge.label,
        "percent": charge.percent,
        "amount": charge.amount,
        "absorbed": charge.absorbed,
    }


def entry_payload(entry: WalletEntry) -> dict[str, Any]:
    """One entry, in the shape every transport answers with.

    Rendered once, here, rather than by each door: three renderings of the same
    row is three places for a field to go missing from one of them.

    The money is deliberately four numbers rather than one. ``gross_amount`` is
    what the customer asked for, ``fee_total`` what it cost, ``net_amount`` what
    they got, and ``amount`` what moved the balance -- which is a different figure
    again whenever a currency was crossed. An app that publishes only the last of
    those cannot answer "why is this 97.10 when I paid 100?", and that is the
    question a payment record exists to answer.
    """
    return {
        "id": entry.pk,
        "kind": entry.kind,
        "direction": entry.direction,
        "method": entry.method,
        "payment_method": entry.payment_method.code if entry.payment_method_id else "",
        "payment_method_name": entry.payment_method.name if entry.payment_method_id else "",
        "network": entry.network.code if entry.network_id else "",
        "network_name": entry.network.name if entry.network_id else "",
        "amount": entry.amount,
        "signed_amount": entry.signed_amount,
        "currency": entry.movement_currency,
        "wallet_currency": entry.wallet.currency,
        "gross_amount": entry.gross_amount,
        "fee_total": entry.fee_total,
        "net_amount": entry.net_amount,
        "charges": [charge_payload(charge) for charge in entry.charges.all()],
        "converted": entry.converted,
        "exchange_rate": entry.exchange_rate,
        "destination": entry.destination,
        "status": entry.status,
        "settled": entry.is_settled,
        # Published beside `settled` rather than left to be inferred from it,
        # because the two differ for a reversed entry -- which is no longer
        # settled and is still part of the balance. A client reconciling a list
        # of movements against the balance needs the second answer, and guessing
        # it from the status is exactly the arithmetic this app exists to do once.
        "counts_towards_balance": entry.counts_towards_balance,
        "approval": entry.approval,
        "awaiting_approval": entry.awaiting_approval,
        "reviewed_at": entry.reviewed_at.isoformat() if entry.reviewed_at else None,
        "review_note": entry.review_note,
        "reference": entry.reference,
        "external_reference": entry.external_reference,
        "description": entry.description,
        "metadata": entry.metadata,
        "counterparty_id": entry.counterparty_id,
        "archived": entry.checkpoint_id is not None,
        "created_at": entry.created_at.isoformat(),
        "settled_at": entry.settled_at.isoformat() if entry.settled_at else None,
    }


def method_payload(
    method: PaymentMethod, *, direction: str | None = None, currency: str | None = None
) -> dict[str, Any]:
    """One configured method, with everything a client needs to offer it.

    Filtered rather than dumped. The currencies and chains that are switched off
    are left out, because a client rendering them would be offering choices this
    app is about to refuse.

    Absorbed fees are not published. They cost the customer nothing -- that is
    what absorbed means -- so they are not part of the price list, and publishing
    them would be telling the world what this deployment's margin is. They are
    still recorded against every entry they touch, where the person who needs them
    is looking.
    """
    wanted = (currency or "").upper()
    currencies = []
    for asset in method.currencies.all():
        if not asset.is_enabled or (wanted and asset.currency != wanted):
            continue
        smallest, largest = asset.bounds()
        currencies.append(
            {
                "currency": asset.currency,
                "min_amount": smallest,
                "max_amount": largest,
                "decimals": asset.display_decimals,
                "networks": [
                    {
                        "code": network.code,
                        "name": network.name,
                        "confirmations": network.confirmations,
                        "network_fee": network.network_fee,
                        "min_amount": network.bounds()[0],
                        "max_amount": network.bounds()[1],
                        "deposit_address": network.deposit_address,
                    }
                    for network in asset.networks.all()
                    if network.is_enabled
                ],
            }
        )

    fees = [
        {
            "kind": fee.kind,
            "label": fee.display_label,
            "applies_to": fee.applies_to,
            "percent": fee.percent,
            "fixed": fee.fixed,
            "basis": fee.basis,
            "minimum": fee.minimum,
            "maximum": fee.maximum,
            "currency": fee.currency.currency if fee.currency_id else "",
        }
        for fee in method.fees.all()
        if fee.is_enabled
        and not fee.absorbed
        and (direction is None or Applies.covers(fee.applies_to, direction))
    ]

    return {
        "code": method.code,
        "name": method.name,
        "rail": method.rail,
        "family": method.family,
        "description": method.description,
        "instructions": method.instructions,
        "icon": method.icon,
        "directions": sorted(
            way for way in (str(Direction.CREDIT), str(Direction.DEBIT)) if method.carries(way)
        ),
        "settles_immediately": method.settles_immediately,
        "reversible": method.reversible,
        "requires_approval": method.requires_approval,
        "needs_network": method.needs_network,
        "needs_destination": method.spec.needs_destination,
        "currencies": currencies,
        "fees": fees,
    }


wallet_service = WalletService()
