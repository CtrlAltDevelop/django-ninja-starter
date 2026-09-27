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

import json
from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal
from typing import Any
from uuid import UUID

from django.conf import settings
from django.db import IntegrityError, transaction
from django.db.models import OuterRef, Subquery
from django.utils import timezone

from apps.wallet import signals
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
    WalletStatus,
    direction_of,
)
from apps.wallet.money import MONEY, ZERO, money

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


#: The kinds an operator may write by hand. A deposit or a withdrawal has a rail
#: and a price and is the account's to ask for; these have neither, and are the
#: back office's to decide.
BACK_OFFICE_KINDS = frozenset(
    {
        str(EntryKind.BONUS),
        str(EntryKind.FEE),
        str(EntryKind.ADJUSTMENT_CREDIT),
        str(EntryKind.ADJUSTMENT_DEBIT),
    }
)

#: The kinds a client may ask for directly. Everything else -- a fee, a
#: chargeback, an adjustment -- is written by the code or the operator that has
#: a reason to, never by the account whose balance it moves.
CLIENT_KINDS = frozenset({str(EntryKind.DEPOSIT), str(EntryKind.WITHDRAWAL)})


#: Re-exported so a transport can `from apps.wallet.services import WalletError`
#: and catch every refusal in one clause. They live in `apps.wallet.errors`
#: because the pricing engine raises them too, and it must not import a service.
__all__ = [
    "ApprovalRequired",
    "BACK_OFFICE_KINDS",
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
    "ExpiryResult",
    "WalletService",
    "entry_payload",
    "expire_after",
    "metadata_limit",
    "page_size",
    "method_payload",
    "wallet_service",
]


#: The longest reason a cancellation or failure may record, like ``review_note``.
REASON_LENGTH = 255


def metadata_limit() -> int:
    """How many bytes of client metadata one movement may carry. Zero means no cap."""
    return int(getattr(settings, "WALLET_MAX_METADATA_BYTES", 4096))


def _check_metadata(metadata: Any) -> None:
    """Refuse metadata that is not an object, or is larger than one movement needs.

    The column is a JSON object and every transport renders it as one, so a list
    or a bare string stored here would be a movement no client could read back.
    Checked in the service because GraphQL's ``JSON`` scalar accepts any shape.

    The size cap matters for the same reason the page size does: this is a field
    the *client* fills in, on every deposit, withdrawal and transfer, and it is
    returned again on every read of that entry. Uncapped, an authenticated
    account can put megabytes into the ledger a movement at a time and make
    everybody's history expensive to read. The default is generous for what the
    field is for -- an order id, a note, a few tags -- and a deployment that
    genuinely needs more can raise it.
    """
    if metadata is None:
        return
    if not isinstance(metadata, dict):
        raise WalletError("metadata has to be an object.")
    forged = sorted(_STAFF_ONLY_KEYS & set(metadata))
    if forged:
        # These say which operator acted; a client writing one would put a
        # staff member's name on a movement they never touched.
        raise WalletError(f"metadata may not set {', '.join(forged)}; the wallet records those.")
    cap = metadata_limit()
    if not cap:
        return
    try:
        size = len(json.dumps(metadata, default=str).encode())
    except (TypeError, ValueError):
        raise WalletError("metadata has to be JSON-serialisable.") from None
    if size > cap:
        raise WalletError(f"metadata is {size} bytes; the most one movement may carry is {cap}.")


def _amount(value: Any) -> Decimal:
    """An amount a column can hold, or :class:`InvalidAmount` -- never a 500.

    ``NaN`` and ``Infinity`` are valid ``Decimal`` values that gRPC's strings and
    GraphQL's ``Decimal`` scalar both let through, and every comparison against
    them raises ``InvalidOperation``. A finite amount with more whole digits than
    the column has fails later still, when rounding or when the database adapts
    it. Both are refused here, at the entry points, before anything compares.
    """
    if value is None:
        raise InvalidAmount("An amount has to be more than zero.")
    amount = Decimal(value)
    whole_digits = MONEY["max_digits"] - MONEY["decimal_places"]
    if not amount.is_finite() or (amount and amount.adjusted() >= whole_digits):
        raise InvalidAmount("That is not an amount this wallet can hold.")
    return amount


def _check_text(**fields: str) -> None:
    """Refuse text longer than its column, instead of a database error.

    SQLite stores an over-long string without complaint; Postgres and MySQL
    raise ``DataError``, which would reach the caller as a 500. The limits are
    read off the model so they cannot drift from it.
    """
    for name, value in fields.items():
        limit = WalletEntry._meta.get_field(name).max_length
        if value and limit and len(value) > limit:
            raise WalletError(f"{name} is {len(value)} characters; the most allowed is {limit}.")


#: Which metadata key records the operator behind each hand-made transition.
#: Settling is where money becomes real, so "who said so" is part of the record
#: rather than something an audit has to reconstruct from a server log. A
#: transition with no operator -- a rail's webhook, an account cancelling its own
#: movement, the expiry job -- records nobody, which is the honest answer.
OPERATOR_KEYS = {
    str(EntryStatus.DONE): "settled_by_operator",
    str(EntryStatus.FAILED): "failed_by_operator",
    str(EntryStatus.EXPIRED): "expired_by_operator",
    str(EntryStatus.CANCELLED): "cancelled_by_operator",
}


def _entry_id(value: Any) -> UUID:
    """An entry id from a caller, where anything that is not a UUID names nothing."""
    try:
        return value if isinstance(value, UUID) else UUID(str(value))
    except ValueError:
        raise WalletNotFound("No such entry.") from None


_STAFF_ONLY_KEYS = frozenset({*OPERATOR_KEYS.values(), "reversed_by_operator", "adjusted_by"})


def _operator_note(status: str, by: Any) -> dict[str, str]:
    """The attribution to merge into an entry's metadata, or nothing."""
    key = OPERATOR_KEYS.get(status)
    if key is None or not getattr(by, "pk", None):
        return {}
    return {key: str(by.pk)}


#: What a rail may report through its signed webhook. See
#: :meth:`WalletService.confirm_from_rail`.
RAIL_EVENTS = frozenset({str(EntryStatus.DONE), str(EntryStatus.FAILED), str(EntryStatus.REVERSED)})


def expire_after() -> timedelta | None:
    """How long a movement may wait on its rail before it is given up on, or ``None``.

    ``DJANGO_WALLET_EXPIRE_AFTER_HOURS``; zero -- the default -- means never. Off
    by default because the right window is a fact about the rails a deployment
    runs: a card authorisation is stale in a day, a bank transfer is not late
    until the third.
    """
    hours = int(getattr(settings, "WALLET_EXPIRE_AFTER_HOURS", 0))
    return timedelta(hours=hours) if hours else None


@dataclass(frozen=True)
class ExpiryResult:
    """What one sweep of stale movements did, and what it deliberately left."""

    expired: int
    """Pending movements nothing confirmed inside the window, now ``expired``."""

    awaiting_operator: int
    """Stale requests left alone: the wait is on a person, not on a rail."""

    window: timedelta | None


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
        priced = quote_movement(
            configured,
            direction=direction,
            amount=_amount(amount),
            currency=(currency or wallet.currency),
            wallet_currency=wallet.currency,
            network_code=network,
        )
        self._check_limits(priced, direction)
        return priced.payload()

    def rates(self, *, base: str | None = None, quote: str | None = None) -> list[dict[str, Any]]:
        """The conversion rates in force, as a customer would be given them.

        The margin is published rather than hidden inside the rate. A deployment
        keeping a spread is entitled to keep one; a deployment that will not say
        it is keeping one is a different thing, and this app does not help with
        that.
        """
        # Only the newest row of each pair, chosen in the database: rates are
        # appended and never edited, so loading them all would load every rate
        # this deployment has ever had, on every call.
        moment = timezone.now()
        newest = ExchangeRate.objects.live(now=moment).filter(
            base=OuterRef("base"), quote=OuterRef("quote")
        )
        live = ExchangeRate.objects.live(now=moment).filter(pk=Subquery(newest.values("pk")[:1]))
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
        return convert(_amount(amount), base, quote, direction=direction).payload()

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

    def recipient(self, account_id: Any) -> Any:
        """The account a transfer is addressed to, as every transport resolves it.

        Unknown, malformed and closed all get the same answer, so the transfer
        form is not a way to find out which account ids exist or are active.
        """
        from django.contrib.auth import get_user_model

        try:
            pk = UUID(str(account_id))
        except ValueError:
            pk = None
        user = get_user_model().objects.filter(pk=pk, is_active=True).first() if pk else None
        if user is None:
            raise WalletNotFound("No such account.")
        return user

    def transfer_to(
        self,
        user: Any,
        account_id: Any,
        *,
        amount: Decimal,
        reference: str,
        description: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """A transfer addressed by account id, which is what every transport has.

        A retry is answered from the first call before the recipient is looked
        up again: if that account has closed since, the money still moved, and
        a 404 would tell the client it had not.
        """
        amount = _amount(amount)
        done = self._existing(self.wallet_for(user), reference) if reference else None
        if (
            done is not None
            and done.kind == str(EntryKind.TRANSFER_OUT)
            and done.counterparty is not None
            and str(done.counterparty.wallet.user_id) == str(account_id)
            and money(done.amount) == money(amount)
        ):
            return entry_payload(done)
        return self.transfer(
            user,
            to_user=self.recipient(account_id),
            amount=amount,
            reference=reference,
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
        # Checked here rather than in each of the three transports that resolve a
        # recipient, because a rule enforced in three places is a rule enforced in
        # two of them a release later. A deactivated account cannot sign in, so it
        # cannot spend what lands in its wallet: the money would simply stop
        # there, which is not a transfer, it is a hole.
        if not getattr(to_user, "is_active", True):
            raise WalletError("That account is closed, so money sent to it could never leave.")
        _check_metadata(metadata)
        _check_text(reference=reference, description=description)
        amount = _amount(amount)

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
                    existing,
                    kind=str(EntryKind.TRANSFER_OUT),
                    amount=money(amount),
                    method="",
                    counterparty=recipient.pk,
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
            self._announce_recorded(out)
            self._announce_recorded(incoming)
            return entry_payload(out)

    # -- moving an entry through its states -------------------------------

    def settle(
        self, user: Any, entry_id: UUID, *, external_reference: str = "", by: Any = None
    ) -> dict[str, Any]:
        """Confirm that a pending movement really happened. This is where money appears.

        A settling withdrawal is re-checked against the balance under the lock:
        between recording and confirming, a chargeback may have taken the money
        away, and paying it out anyway is the one mistake a wallet cannot undo.

        ``by`` is the operator who said so, recorded on the entry. This is the
        single most consequential thing anybody in the back office can do -- it
        turns a row into money -- so it is not left to a server log that rotates.
        A settlement with no operator came from a rail's signed confirmation,
        which carries its own `external_reference` instead.
        """
        return self._transition(
            user, entry_id, str(EntryStatus.DONE), external_reference=external_reference, by=by
        )

    def fail(
        self, user: Any, entry_id: UUID, *, reason: str = "", by: Any = None
    ) -> dict[str, Any]:
        """The rail refused it. Terminal, and it never counted for anything."""
        return self._transition(user, entry_id, str(EntryStatus.FAILED), reason=reason, by=by)

    def cancel(self, user: Any, entry_id: UUID, *, reason: str = "") -> dict[str, Any]:
        """Withdraw a movement before it settles. Only ever a pending entry."""
        return self._transition(user, entry_id, str(EntryStatus.CANCELLED), reason=reason)

    def expire(self, user: Any, entry_id: UUID) -> dict[str, Any]:
        """Give up on a pending movement nothing ever confirmed."""
        return self._transition(user, entry_id, str(EntryStatus.EXPIRED))

    def expire_entry(self, entry_id: UUID, *, reason: str = "", by: Any = None) -> dict[str, Any]:
        """Give up on one pending movement from the back office, whichever wallet it is in.

        Expiring asserts that the money did *not* move, which is the safe
        direction: it releases whatever a pending payout was holding, and a
        deposit whose money turns up after all arrives as a fresh movement
        rather than resurrecting this one. Idempotent, like every other
        transition a scheduled job can repeat.
        """
        with transaction.atomic():
            entry = self._locked_across_wallets(entry_id)
            if entry.status == str(EntryStatus.EXPIRED):
                return entry_payload(entry)
            if not entry.can_become(str(EntryStatus.EXPIRED)):
                raise InvalidTransition(
                    f"A {entry.get_status_display().lower()} entry cannot expire."
                )
            entry.status = str(EntryStatus.EXPIRED)
            fields = ["status"]
            note = {
                **({"reason": reason} if reason else {}),
                **_operator_note(str(EntryStatus.EXPIRED), by),
            }
            if note:
                entry.metadata = {**entry.metadata, **note}
                fields.append("metadata")
            entry.save(update_fields=fields)
            signals.announce(signals.entry_expired, entry=entry_payload(entry, staff=True))
            return entry_payload(entry)

    def expire_stale(self, *, older_than: timedelta | None = None, now: Any = None) -> ExpiryResult:
        """Expire every movement that has waited on its rail longer than the window.

        What ``manage.py wallet_expire`` runs. Without it, a card deposit whose
        webhook never came stays pending for ever -- and a pending *payout* holds
        its money out of ``available`` for ever with it.

        **A request waiting on an operator is not expired**, however old. Its
        status and its approval are two separate waits, and this sweep is about
        the first: a rail that never answered. A request nobody has looked at is
        a queue problem, and expiring it would quietly empty the queue that is
        supposed to make somebody look. Those are counted and reported instead.

        Each movement is expired in its own transaction under its own wallet's
        lock, so one wallet with a busy afternoon does not hold up the rest, and
        a movement a webhook settled in the meantime is simply skipped.
        """
        window = older_than if older_than is not None else expire_after()
        if window is None:
            return ExpiryResult(expired=0, awaiting_operator=0, window=None)
        cutoff = (now or timezone.now()) - window
        stale = WalletEntry.objects.pending().filter(created_at__lt=cutoff)
        waiting = stale.filter(approval=str(Approval.REQUESTED)).count()
        expired = 0
        for entry_id in stale.exclude(approval=str(Approval.REQUESTED)).values_list(
            "pk", flat=True
        ):
            try:
                self.expire_entry(entry_id, reason="Not confirmed in time.")
            except WalletError:
                # Settled, failed or cancelled between the listing and the lock.
                continue
            expired += 1
        return ExpiryResult(expired=expired, awaiting_operator=waiting, window=window)

    # -- the back office --------------------------------------------------

    def adjust(
        self,
        user: Any,
        *,
        kind: str,
        amount: Decimal,
        reference: str,
        reason: str,
        by: Any = None,
    ) -> dict[str, Any]:
        """Correct a balance by hand: a bonus, a fee, a goodwill credit, a write-off.

        Settled at once and priced at nothing -- there is no rail and no
        processor, only an operator's decision -- and still written the way
        every other movement is: under the wallet's lock, once per reference,
        with the funds checked before anything is taken out. A debit adjustment
        that would take a wallet below its overdraft allowance is refused, not
        recorded; clawing back money the account has already spent is a
        conversation, not a row.

        ``reason`` is required. An unexplained balance change is the one entry
        an auditor will certainly ask about, and the person who made it will not
        remember.
        """
        if kind not in BACK_OFFICE_KINDS:
            raise WalletError(
                f"{kind!r} is not something the back office writes by hand. "
                f"Use one of: {', '.join(sorted(BACK_OFFICE_KINDS))}."
            )
        if not reason.strip():
            raise WalletError("An adjustment needs a reason; it is the only record of why.")
        metadata: dict[str, Any] = {"reason": reason}
        if getattr(by, "pk", None):
            metadata["adjusted_by"] = str(by.pk)
        return self._record_system(
            user,
            kind=kind,
            amount=amount,
            reference=reference,
            description=reason,
            metadata=metadata,
        )

    def pay(
        self,
        user: Any,
        *,
        amount: Decimal,
        reference: str,
        description: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Spend from the wallet on something this project sells.

        For another app in the same project -- a shop checking out against the
        balance, a subscription renewing -- and published on no transport: what
        is being bought, and for how much, is that app's to decide, and a route
        that let an account name its own price would be a route that bought
        things for nothing. Free and settled at once, like a transfer, because
        the money never leaves this deployment.
        """
        _check_metadata(metadata)
        return self._record_system(
            user,
            kind=str(EntryKind.PAYMENT),
            amount=amount,
            reference=reference,
            description=description,
            metadata=metadata or {},
        )

    def set_wallet_status(
        self, wallet_id: UUID, status: str, *, by: Any = None, reason: str = ""
    ) -> dict[str, Any]:
        """Freeze, unfreeze or close a wallet, with the checks a status change needs.

        **Frozen** refuses payouts and still takes money in, which is what a
        compliance hold means. **Closed** takes nothing either way, and so it is
        refused while there is anything left to strand: a settled balance that
        is not zero, or a movement still pending that would have nowhere to
        land. Pay it out or adjust it to zero first. A closed wallet stays
        closed -- reopening one is opening a new account's history on top of an
        old one's, which is a decision for somebody with more context than a
        status field.
        """
        if status not in WalletStatus.values:
            raise WalletError(f"{status!r} is not a wallet status.")
        with transaction.atomic():
            locked = Wallet.objects.select_for_update().filter(pk=wallet_id).first()
            if locked is None:
                raise WalletNotFound("No such wallet.")
            previous = locked.status
            if previous == status:
                return {"id": locked.pk, "status": status, "previous": previous}
            if previous == str(WalletStatus.CLOSED):
                raise InvalidTransition("A closed wallet stays closed.")
            if status == str(WalletStatus.CLOSED):
                balance = balance_of(locked)
                if balance.has_pending:
                    raise InvalidTransition(
                        "This wallet has movements still pending, and a closed wallet "
                        "would give them nowhere to land. Settle, fail or expire them first."
                    )
                if balance.settled != ZERO:
                    raise InvalidTransition(
                        f"This wallet still holds {balance.settled} {locked.currency}. Pay "
                        "it out or adjust it to zero before closing it."
                    )
            locked.status = status
            locked.save(update_fields=["status", "updated_at"])
            change = {
                "id": locked.pk,
                "status": status,
                "previous": previous,
                "reason": reason,
                "by": str(by.pk) if getattr(by, "pk", None) else None,
            }
            signals.announce(signals.wallet_status_changed, wallet=change)
            return {"id": locked.pk, "status": status, "previous": previous}

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
            # A payout approved on a frozen wallet would be handed over at the
            # counter, or announced as `payout_ready` to a rail that sends it --
            # money leaving during the hold the freeze exists to impose.
            self._check_wallet(entry.wallet, entry.direction)
            entry.approval = str(Approval.APPROVED)
            entry.reviewed_by = by if getattr(by, "pk", None) else None
            entry.reviewed_at = timezone.now()
            entry.review_note = note
            entry.save(update_fields=["approval", "reviewed_by", "reviewed_at", "review_note"])
            signals.announce(signals.entry_approved, entry=entry_payload(entry, staff=True))

            rail = entry.payment_method.spec if entry.payment_method_id else None
            if (
                rail is not None
                and rail.settles_immediately
                and entry.can_become(str(EntryStatus.DONE))
            ):
                return self._settle_locked(entry)
            if _payout_ready(entry):
                signals.announce(signals.payout_ready, entry=entry_payload(entry, staff=True))
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

        Three events, because there are three things a rail knows: the money
        moved, it did not, or -- on a rail where the payer can take it back, a
        card's chargeback -- it moved and was later taken back. Approval is not
        among them: a movement waiting on an operator stays waiting however
        loudly its processor confirms it, which is exactly what :meth:`settle`
        already refuses and this inherits.

        ``reversed`` is only believed from a rail whose type can be reversed.
        A bank transfer or a chain confirmation is final, and a processor
        claiming otherwise is a misconfiguration or a forgery -- either way not
        a reason to take money out of somebody's wallet.
        """
        if event not in RAIL_EVENTS:
            raise WalletError(
                "A rail may only report that a movement settled, failed or was reversed; "
                f"{event!r} is none of those."
            )
        with transaction.atomic():
            entry = self._locked_across_wallets(entry_id)
            through = entry.payment_method.code if entry.payment_method_id else ""
            if through != method_code:
                # Deliberately the same answer as an entry that does not exist:
                # a rail probing for other processors' entry ids should not be
                # able to tell "not yours" from "no such thing".
                raise WalletNotFound("No such entry.")
            if event == str(EntryStatus.REVERSED):
                if not entry.payment_method.reversible:
                    raise WalletError(
                        f"A {entry.payment_method.name} payment cannot be taken back, so "
                        "its rail cannot report one reversed."
                    )
                reference = _reversal_reference(entry.pk)
                if entry.status == event and self._existing(entry.wallet, reference) is None:
                    # Undone already, by an operator: the rail is late, not wrong.
                    return entry_payload(entry)
                return self._reverse_locked(
                    entry.wallet,
                    entry.pk,
                    reference=reference,
                    reason=reason,
                    external_reference=external_reference,
                )
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
            signals.announce(signals.entry_failed, entry=entry_payload(entry, staff=True))
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
            cancelled = entry.status == str(EntryStatus.PENDING)
            if cancelled:
                entry.status = str(EntryStatus.CANCELLED)
                fields.append("status")
            entry.save(update_fields=fields)
            signals.announce(signals.entry_rejected, entry=entry_payload(entry, staff=True))
            if cancelled:
                signals.announce(signals.entry_cancelled, entry=entry_payload(entry, staff=True))
            return entry_payload(entry)

    def reverse(
        self, user: Any, entry_id: UUID, *, reference: str, reason: str = ""
    ) -> dict[str, Any]:
        """Undo a settled movement of this account's -- as a second entry, never as an edit.

        A chargeback, a returned transfer, a refund of a payment. The original
        stays exactly as it was and is marked ``reversed``; the money moves back
        on a new entry pointing at it. A ledger that rewrote the original could
        not be reconciled against the rail that still remembers it happening.

        Scoped to the account for code that already holds one. It is published
        on no transport -- a customer who could reverse their own paid-out
        withdrawal would be paid twice. The back office reaches the same rule
        through :meth:`reverse_entry`, and a rail through :meth:`confirm_from_rail`.
        """
        wallet = self.wallet_for(user)
        with transaction.atomic():
            locked = Wallet.objects.select_for_update().get(pk=wallet.pk)
            return self._reverse_locked(locked, entry_id, reference=reference, reason=reason)

    def reverse_entry(
        self, entry_id: UUID, *, reference: str = "", reason: str = "", by: Any = None
    ) -> dict[str, Any]:
        """Undo a settled movement from the back office, whichever wallet it is in.

        What an operator does when a bank returns a transfer or a dispute is lost
        and no webhook is coming to say so. The reference defaults to one derived
        from the entry, so a double-clicked action returns the first correction
        rather than writing a second one.
        """
        with transaction.atomic():
            original = self._locked_across_wallets(entry_id)
            return self._reverse_locked(
                original.wallet,
                original.pk,
                reference=reference or _reversal_reference(original.pk),
                reason=reason,
                by=by,
            )

    def _reverse_locked(
        self,
        locked: Wallet,
        entry_id: UUID,
        *,
        reference: str,
        reason: str = "",
        external_reference: str = "",
        by: Any = None,
    ) -> dict[str, Any]:
        """The one reversal, with the wallet already locked by the caller."""
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
        if original.method == str(Method.INTERNAL):
            # Reversing one half of a transfer credits the sender and leaves the
            # recipient holding the money: a reversal that creates money. The
            # honest undo is the recipient transferring it back.
            raise InvalidTransition(
                "A transfer between two wallets here is undone by transferring the money "
                "back, not by reversing one side of it."
            )
        if not original.can_become(str(EntryStatus.REVERSED)):
            raise InvalidTransition(
                f"A {original.get_status_display().lower()} entry cannot be reversed."
            )
        opposite = (
            str(EntryKind.CHARGEBACK)
            if original.direction == str(Direction.CREDIT)
            else str(EntryKind.REFUND)
        )
        metadata: dict[str, Any] = {"reversal_of": str(original.pk), "reason": reason}
        if getattr(by, "pk", None):
            metadata["reversed_by_operator"] = str(by.pk)
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
            external_reference=external_reference or original.external_reference,
            description=reason or f"Reversal of {original.pk}",
            metadata=metadata,
            counterparty=original,
        )
        original.status = str(EntryStatus.REVERSED)
        original.metadata = {**original.metadata, "reversed_by": str(correction.pk)}
        original.save(update_fields=["status", "metadata"])
        signals.announce(
            signals.entry_reversed,
            entry=entry_payload(original, staff=True),
            correction=entry_payload(correction, staff=True),
        )
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
        self,
        existing: WalletEntry,
        *,
        kind: str,
        amount: Decimal,
        method: str,
        counterparty: Any = None,
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
            or (counterparty is not None and existing.counterparty.wallet_id != counterparty)
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

    def _check_amount(
        self, amount: Decimal, direction: str, *, ceiling_on: Decimal | None = None
    ) -> None:
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
        if largest and (amount if ceiling_on is None else ceiling_on) > largest:
            raise InvalidAmount(f"The largest allowed here is {largest}.")

    def _check_limits(self, priced: Quote, direction: str) -> None:
        """The deployment-wide limits against a priced movement, as recording checks them.

        Shared by :meth:`_record` and :meth:`quote`, so an amount quoted is an
        amount that will be accepted.

        The ceiling is on the figure the customer named, in the wallet's
        currency: for a deposit that is before the charges come off, or a gross
        just over it would pass because the fees brought the net under it. The
        floor stays on what actually lands, so fees cannot carry a deposit
        below the minimum.
        """
        named = (
            money(priced.gross * priced.conversion.effective_rate)
            if direction == str(Direction.CREDIT)
            else priced.wallet_amount
        )
        self._check_amount(priced.wallet_amount, direction, ceiling_on=named)

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
        _check_metadata(metadata)
        _check_text(
            reference=reference,
            external_reference=external_reference,
            description=description,
            destination=destination,
        )
        direction = direction_of(kind)
        configured = self._method(method, direction)
        wallet = self.wallet_for(user)

        priced = quote_movement(
            configured,
            direction=direction,
            amount=_amount(amount),
            currency=(currency or wallet.currency),
            wallet_currency=wallet.currency,
            network_code=network,
        )
        self._check_destination(priced, direction, destination)
        self._check_limits(priced, direction)

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
            self._announce_recorded(entry)
            return entry_payload(entry)

    def _record_system(
        self,
        user: Any,
        *,
        kind: str,
        amount: Decimal,
        reference: str,
        description: str,
        metadata: dict[str, Any],
    ) -> dict[str, Any]:
        """Write one movement that no rail carries and nothing prices.

        The unpriced sibling of :meth:`_record`, for what the project itself
        writes: settled on the spot, no charges, the ``system`` rail. The
        deployment's deposit and withdrawal limits do not apply -- they bound
        what an account may move, and this is not an account moving anything --
        but the wallet's status and its funds do, under the lock, as always.
        """
        if not reference:
            raise WalletError("A reference is required; it is what makes a retry safe.")
        _check_text(reference=reference, description=description)
        amount = _amount(amount)
        if amount <= ZERO:
            raise InvalidAmount("An amount has to be more than zero.")
        direction = direction_of(kind)
        moving = money(amount)
        wallet = self.wallet_for(user)
        with transaction.atomic():
            locked = Wallet.objects.select_for_update().get(pk=wallet.pk)
            if existing := self._existing(locked, reference):
                self._check_same_request(existing, kind=kind, amount=moving, method="")
                return entry_payload(existing)
            self._check_wallet(locked, direction)
            if direction == str(Direction.DEBIT):
                self._check_funds(locked, moving)
            entry = WalletEntry.objects.create(
                wallet=locked,
                kind=kind,
                method=str(Method.SYSTEM),
                amount=moving,
                gross_amount=moving,
                fee_total=ZERO,
                status=str(EntryStatus.DONE),
                approval=str(Approval.NOT_REQUIRED),
                reference=reference,
                description=description,
                metadata=metadata,
            )
            self._announce_recorded(entry)
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
            .filter(pk=_entry_id(entry_id))
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
            .filter(wallet=wallet, pk=_entry_id(entry_id))
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
            WalletEntry.objects.filter(pk=_entry_id(entry_id))
            .values_list("wallet_id", flat=True)
            .first()
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
        signals.announce(signals.entry_settled, entry=entry_payload(entry, staff=True))
        return entry_payload(entry)

    def _announce_recorded(self, entry: WalletEntry) -> None:
        """Tell the project a movement exists, and whatever that already means."""
        payload = entry_payload(entry, staff=True)
        signals.announce(signals.entry_recorded, entry=payload)
        if entry.status == str(EntryStatus.DONE):
            signals.announce(signals.entry_settled, entry=payload)
        elif _payout_ready(entry):
            signals.announce(signals.payout_ready, entry=payload)

    def _transition(
        self,
        user: Any,
        entry_id: UUID,
        status: str,
        *,
        external_reference: str = "",
        reason: str = "",
        by: Any = None,
    ) -> dict[str, Any]:
        """Move one entry to a new status, if the map allows it.

        Idempotent where it can honestly be: asking for the status an entry is
        already in is success rather than an error, because the webhook that
        delivers a confirmation will deliver it twice.
        """
        # The reason lands in metadata, which the recording path caps; a cancel
        # with a multi-megabyte reason would otherwise go around that cap.
        if len(reason) > REASON_LENGTH:
            raise WalletError(f"A reason may be at most {REASON_LENGTH} characters.")
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
            # Once `payout_ready` has fired the rail may already be sending the
            # money; cancelling would release the hold and the payout would be
            # paid twice. Only the rail or an operator can end it from here.
            if status == str(EntryStatus.CANCELLED) and _payout_ready(entry):
                raise InvalidTransition(
                    "This payout has already been sent to be paid, so it can no "
                    "longer be cancelled."
                )
            if status == str(EntryStatus.DONE) and entry.direction == str(Direction.DEBIT):
                # Re-checked at settlement, not only at recording: money can have
                # gone between the two, and a payout is not recallable. Its own
                # hold is handed back first -- it is already inside `outgoing`.
                # Only operators settle through here -- a rail's `done` goes
                # through `confirm_from_rail`, and is believed on a frozen wallet
                # because that money has already left -- so a freeze stops it.
                self._check_wallet(locked, entry.direction)
                self._check_funds(locked, entry.amount, already_held=entry.amount)

            entry.status = status
            fields = ["status"]
            if external_reference:
                entry.external_reference = external_reference
                fields.append("external_reference")
            note = {**({"reason": reason} if reason else {}), **_operator_note(status, by)}
            if note:
                entry.metadata = {**entry.metadata, **note}
                fields.append("metadata")
            entry.save(update_fields=fields)
            signals.announce(signals.STATUS_SIGNALS[status], entry=entry_payload(entry, staff=True))
            return entry_payload(entry)


def _payout_ready(entry: WalletEntry) -> bool:
    """Whether a payout now waits on nothing but its rail being told to pay.

    Pending, cleared of approval, and through a configured method -- the three
    together, because each alone is true of rows nobody should send money for:
    a payout still awaiting an operator is pending, and a cash payout handed
    over at a counter went through a method and settled on the spot.
    """
    return (
        entry.direction == str(Direction.DEBIT)
        and entry.status == str(EntryStatus.PENDING)
        and entry.is_cleared
        and entry.payment_method_id is not None
    )


def _reversal_reference(entry_id: Any) -> str:
    """The reference a back-office or rail reversal writes, derived so a retry finds it."""
    return f"reversal:{entry_id}"


def charge_payload(charge: Any) -> dict[str, Any]:
    """One fee line, as it was charged."""
    return {
        "kind": charge.kind,
        "label": charge.label,
        "percent": charge.percent,
        "amount": charge.amount,
        "absorbed": charge.absorbed,
    }


def entry_payload(entry: WalletEntry, *, staff: bool = False) -> dict[str, Any]:
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
        # Who in the back office acted stays on the row for the admin and audit;
        # a customer reading their own movement is not told staff account ids.
        # Signals pass `staff=True`, so a project's audit receiver still learns
        # who acted.
        "metadata": entry.metadata
        if staff
        else {k: v for k, v in entry.metadata.items() if k not in _STAFF_ONLY_KEYS},
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
