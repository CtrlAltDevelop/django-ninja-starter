"""The contract the wallet endpoints publish.

The shape worth pointing at is :class:`BalanceOut`, which answers with more than
one number. That is not a convenience: a wallet holding 100 with a pending
withdrawal of 40 and a pending deposit of 25 has three defensible "balances", and
an API that picked one for the client would be picking wrong for somebody. So all
of them are named, and the one a client must authorise against -- ``available``
-- is the one whose meaning is spelled out.

Every amount is a ``Decimal``. Money in a float is a rounding error waiting for a
customer to find it, and JSON's number type is a float everywhere it lands.
"""

from datetime import datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from ninja import Schema

from apps.wallet.catalog import Applies, Basis, ChargeKind
from apps.wallet.methods import Direction, Family, Method
from apps.wallet.models import Approval, EntryKind, EntryStatus, WalletStatus


class BalanceOut(Schema):
    """What a wallet holds, what is on its way, and what may be spent."""

    currency: str
    settled: Decimal
    """What the wallet actually holds, once everything pending is set aside."""

    available: Decimal
    """`settled` less pending withdrawals. Authorise against this one."""

    incoming: Decimal
    """Recorded and not yet confirmed, inbound. Worth nothing yet."""

    outgoing: Decimal
    """Recorded and not yet confirmed, outbound. Already spoken for."""

    projected: Decimal
    """Where this lands if everything outstanding succeeds. Show it; never authorise on it."""

    has_pending: bool
    checkpoint_sequence: int
    """Which archive checkpoint this was derived from. 0 before the first one is cut."""

    unarchived_entries: int
    """How many entries were re-summed on top of that checkpoint."""


class WalletOut(Schema):
    """The wallet screen: the account's wallet and both of its numbers."""

    id: UUID
    currency: str
    status: WalletStatus
    created_at: datetime
    balance: BalanceOut


class ChargeOut(Schema):
    """One fee, as it was actually charged on this movement."""

    kind: ChargeKind
    label: str
    percent: Decimal
    """The rate applied at the time. Zero for a flat charge."""

    amount: Decimal
    absorbed: bool
    """True when this deployment paid it rather than you. It changed nothing you received."""


class EntryOut(Schema):
    """One movement of money, what it cost, and what became of it.

    The money is four numbers rather than one, and the difference between them is
    the whole point of a payment record: `gross_amount` is what you asked for,
    `fee_total` what it cost, `net_amount` what survived, and `amount` what moved
    the wallet -- which differs again whenever a currency was crossed.
    """

    id: UUID
    kind: EntryKind
    direction: Direction
    method: Method
    """The kind of rail this travelled on."""

    payment_method: str = ""
    """The configured method's code. Empty for transfers and system entries."""

    payment_method_name: str = ""
    network: str = ""
    """For crypto: the chain it moved on."""

    network_name: str = ""
    amount: Decimal
    """What moved the balance, in the wallet's currency. Always positive."""

    signed_amount: Decimal
    """The same amount as it moves the balance: negative when money left."""

    currency: str
    """The currency you named the movement in."""

    wallet_currency: str
    gross_amount: Decimal
    """What you asked for, before any charge came off it."""

    fee_total: Decimal
    net_amount: Decimal
    """`gross_amount` less the charges: what actually reached the other side."""

    charges: list[ChargeOut] = []
    converted: bool
    exchange_rate: Decimal | None = None
    """The rate this converted at, spread included. Null when no currency was crossed."""

    destination: str = ""
    status: EntryStatus
    settled: bool
    """True only for `done`."""

    counts_towards_balance: bool
    """Whether this is part of the balance. A `reversed` entry still is: it
    happened, and the entry written to undo it is what takes the money back.
    Add the `signed_amount` of every movement with this set and you get
    `settled` from the balance."""

    approval: Approval
    awaiting_approval: bool
    """True while an operator still has to apply this. It cannot settle until they do."""

    reviewed_at: datetime | None = None
    review_note: str = ""
    reference: str
    external_reference: str
    description: str
    metadata: dict[str, Any] = {}
    counterparty_id: UUID | None = None
    """The other half of a transfer, or the entry this one reverses."""

    archived: bool
    """Whether a checkpoint has already counted this towards the balance."""

    created_at: datetime
    settled_at: datetime | None = None


class EntryPage(Schema):
    """A page of movements, and how many there were to page through."""

    entries: list[EntryOut]
    total: int
    limit: int
    offset: int


class CheckpointOut(Schema):
    """One archived run of entries, and the balance it left behind."""

    id: UUID
    sequence: int
    balance: Decimal
    credited: Decimal
    debited: Decimal
    entry_count: int
    created_at: datetime


class NetworkOut(Schema):
    """One chain an asset moves on.

    Never guess at this. The same asset on two chains is one balance to you and
    two incompatible destinations to the network, and paying to an address on the
    wrong one does not fail -- it delivers the money to nobody.
    """

    code: str
    name: str
    confirmations: int
    """How many blocks a deposit waits for before it settles."""

    network_fee: Decimal
    min_amount: Decimal
    max_amount: Decimal
    """Zero means no ceiling."""

    deposit_address: str = ""


class CurrencyOut(Schema):
    """A currency a method takes, and the limits that apply in it."""

    currency: str
    min_amount: Decimal
    max_amount: Decimal
    """Zero means no ceiling."""

    decimals: int
    """How many decimal places to show. Two for money, more for a coin."""

    networks: list[NetworkOut] = []


class FeeOut(Schema):
    """One component of what a method charges.

    Published so a client can show a cost before a customer commits to one. Fees
    this deployment absorbs are not listed, because they cost you nothing.
    """

    kind: ChargeKind
    label: str
    applies_to: Applies
    percent: Decimal
    fixed: Decimal
    basis: Basis
    """What the percentage is of. `charges` means it is charged on the fees, as VAT is."""

    minimum: Decimal
    maximum: Decimal
    currency: str = ""
    """Empty when the fee applies to every currency the method takes."""


class MethodOut(Schema):
    """One configured way to pay, with everything needed to offer it.

    Read this rather than hard-coding a list. What this deployment takes is
    configuration an administrator edits, so a client that assumes a fixed set
    breaks the afternoon one is turned on.
    """

    code: str
    name: str
    rail: Method
    family: Family
    """The coarse grouping a customer recognises: cash, bank, card, wallet, crypto."""

    description: str
    instructions: str
    """What the customer has to do after choosing this. Shown once they have."""

    icon: str = ""
    directions: list[Direction]
    settles_immediately: bool
    """Whether a movement this way is `done` at once, or waits on a confirmation."""

    reversible: bool
    """Whether the counterparty can take it back later -- a chargeback, a return."""

    requires_approval: bool
    """Whether a movement through this is a request until an operator applies it."""

    needs_network: bool
    needs_destination: bool
    currencies: list[CurrencyOut] = []
    fees: list[FeeOut] = []


class ExchangeOut(Schema):
    """One amount converted into another currency, and the arithmetic that did it."""

    base: str
    quote: str
    rate: Decimal
    """The market rate, before the spread."""

    margin_percent: Decimal
    """The spread kept on the conversion. Always taken against you."""

    effective_rate: Decimal
    """What the conversion actually used: the rate with the spread applied."""

    amount: Decimal
    converted: Decimal
    inverted: bool
    """True when this pair was read backwards off its reciprocal."""


class RateOut(Schema):
    """A conversion rate in force."""

    base: str
    quote: str
    rate: Decimal
    margin_percent: Decimal
    source: str = ""
    effective_from: datetime


class QuoteOut(Schema):
    """What a movement would cost and produce, before anything is written.

    The same calculation the recording path makes. An amount quoted here is the
    amount charged, so a client can show it and be believed.
    """

    method: str
    method_name: str
    rail: str
    direction: Direction
    currency: str
    wallet_currency: str
    network: str = ""
    gross: Decimal
    charges: list[ChargeOut] = []
    fee_total: Decimal
    absorbed_total: Decimal
    net: Decimal
    """What survives the charges, in `currency`."""

    wallet_amount: Decimal
    """What would move the balance, in the wallet's own currency."""

    settlement_amount: Decimal
    """What moves on the rail: what you pay in, or what gets paid out."""

    converted: bool
    exchange: ExchangeOut
    requires_approval: bool
    settles_immediately: bool


class QuoteIn(Schema):
    """Ask what a movement would cost, without making one."""

    method: str
    direction: Direction
    amount: Decimal
    currency: str = ""
    """Defaults to the wallet's own currency."""

    network: str = ""


class MoveIn(Schema):
    """A deposit or a withdrawal, as a client asks for one."""

    amount: Decimal
    """What you are asking to move, before charges. The figure a customer typed."""

    method: str
    """The configured method's code, from `GET /wallet/methods`."""

    reference: str
    """Your idempotency key, unique per wallet. Retrying with the same one returns
    the entry the first call made rather than moving the money twice."""

    currency: str = ""
    """Defaults to the wallet's own currency. Anything else is converted at the live rate."""

    network: str = ""
    """Required for crypto, and never guessed at. See `NetworkOut`."""

    destination: str = ""
    """Where a payout goes: an address, an account number. Ignored for deposits."""

    external_reference: str = ""
    description: str = ""
    metadata: dict[str, Any] = {}


class TransferIn(Schema):
    """Money moved to another account's wallet in this same app."""

    to_user_id: str
    amount: Decimal
    reference: str
    description: str = ""
    metadata: dict[str, Any] = {}


class SettleIn(Schema):
    external_reference: str = ""
    """What the rail called it, if the confirmation carried an id."""


class ReasonIn(Schema):
    reason: str = ""


class ReverseIn(Schema):
    reference: str
    """The idempotency key for the correcting entry this writes."""

    reason: str = ""


class RailEventIn(Schema):
    """What a payment rail says happened, in the one shape this app accepts.

    Deliberately small. A rail knows two things about a movement -- the money
    arrived, or it did not -- and a webhook that accepted anything richer would
    be letting a processor drive a state machine it cannot see.
    """

    entry_id: UUID
    """The movement this is about: the id this app gave the rail when it started."""

    event: str
    """`done` or `failed`. Nothing else is a thing a rail knows."""

    external_reference: str = ""
    """What the rail calls it, kept on the entry so the two can be reconciled."""

    reason: str = ""
    """Why it failed, for the record and for whoever asks about it later."""
