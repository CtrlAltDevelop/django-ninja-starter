"""GraphQL types for the wallet, its movements, and what they cost.

The same shapes the REST schemas publish, in Strawberry's vocabulary, built from
the same payload dictionaries the service returns -- so a field cannot exist on
one transport and be missing from the other by accident.

``metadata`` travels as the ``JSON`` scalar because its shape belongs to whoever
integrated the rail. A union of every canonical shape would have to be edited the
first time somebody stores something new in it, and would make every generated
client worse in the meantime.
"""

from datetime import datetime
from decimal import Decimal
from typing import Any

import strawberry
from strawberry.scalars import JSON


@strawberry.type
class BalanceType:
    """What a wallet holds, what is on its way, and what may be spent.

    Several numbers rather than one, because a wallet holding 100 with a pending
    withdrawal of 40 has more than one defensible balance, and picking one for
    the client would be picking wrong for somebody.
    """

    currency: str
    settled: Decimal
    available: Decimal
    """`settled` less what pending payouts have claimed. Authorise against this one."""

    incoming: Decimal
    outgoing: Decimal
    projected: Decimal
    """Where this lands if everything outstanding succeeds. Never authorise on it."""

    has_pending: bool
    checkpoint_sequence: int
    unarchived_entries: int


@strawberry.type
class WalletType:
    id: str
    currency: str
    status: str
    created_at: datetime
    balance: BalanceType


@strawberry.type
class ChargeType:
    """One fee, as it was actually charged on this movement."""

    kind: str
    label: str
    percent: Decimal
    amount: Decimal
    absorbed: bool
    """True when this deployment paid it. It changed nothing the customer received."""


@strawberry.type
class EntryType:
    """One movement of money, what it cost, and what became of it."""

    id: str
    kind: str
    direction: str
    method: str
    payment_method: str
    payment_method_name: str
    network: str
    network_name: str
    amount: Decimal
    """What moved the balance, in the wallet's currency, after charges and conversion."""

    signed_amount: Decimal
    currency: str
    wallet_currency: str
    gross_amount: Decimal
    """What the customer asked for, before any charge came off it."""

    fee_total: Decimal
    net_amount: Decimal
    charges: list[ChargeType]
    converted: bool
    exchange_rate: Decimal | None
    destination: str
    status: str
    settled: bool
    counts_towards_balance: bool
    """Whether this is part of the balance. A reversed entry still is."""

    approval: str
    awaiting_approval: bool
    """True while an operator still has to apply this. It cannot settle until they do."""

    reviewed_at: datetime | None
    review_note: str
    reference: str
    external_reference: str
    description: str
    metadata: JSON
    counterparty_id: str | None
    archived: bool
    created_at: datetime
    settled_at: datetime | None


@strawberry.type
class EntryPageType:
    entries: list[EntryType]
    total: int
    limit: int
    offset: int


@strawberry.type
class CheckpointType:
    """One archived run of entries, and the balance it left behind."""

    id: str
    sequence: int
    balance: Decimal
    credited: Decimal
    debited: Decimal
    entry_count: int
    created_at: datetime


@strawberry.type
class NetworkType:
    """One chain an asset moves on. Never guess at this one."""

    code: str
    name: str
    confirmations: int
    network_fee: Decimal
    min_amount: Decimal
    max_amount: Decimal
    deposit_address: str


@strawberry.type
class CurrencyType:
    currency: str
    min_amount: Decimal
    max_amount: Decimal
    """Zero means no ceiling."""

    decimals: int
    networks: list[NetworkType]


@strawberry.type
class FeeType:
    """One component of what a method charges. Absorbed fees are not published."""

    kind: str
    label: str
    applies_to: str
    percent: Decimal
    fixed: Decimal
    basis: str
    minimum: Decimal
    maximum: Decimal
    currency: str


@strawberry.type
class MethodType:
    """One configured way to pay, and everything needed to offer it."""

    code: str
    name: str
    rail: str
    family: str
    description: str
    instructions: str
    icon: str
    directions: list[str]
    settles_immediately: bool
    reversible: bool
    requires_approval: bool
    needs_network: bool
    needs_destination: bool
    currencies: list[CurrencyType]
    fees: list[FeeType]


@strawberry.type
class ExchangeType:
    """One amount converted, and the arithmetic that did it."""

    base: str
    quote: str
    rate: Decimal
    margin_percent: Decimal
    effective_rate: Decimal
    amount: Decimal
    converted: Decimal
    inverted: bool


@strawberry.type
class RateType:
    base: str
    quote: str
    rate: Decimal
    margin_percent: Decimal
    source: str
    effective_from: datetime


@strawberry.type
class QuoteType:
    """What a movement would cost and produce, before anything is written."""

    method: str
    method_name: str
    rail: str
    direction: str
    currency: str
    wallet_currency: str
    network: str
    gross: Decimal
    charges: list[ChargeType]
    fee_total: Decimal
    absorbed_total: Decimal
    net: Decimal
    wallet_amount: Decimal
    settlement_amount: Decimal
    converted: bool
    exchange: ExchangeType
    requires_approval: bool
    settles_immediately: bool


def _moment(value: Any) -> Any:
    """A timestamp the service rendered as text, back into one Strawberry can serialise."""
    return datetime.fromisoformat(value) if value else None


def balance_type(row: dict[str, Any]) -> BalanceType:
    return BalanceType(**row)


def wallet_type(row: dict[str, Any]) -> WalletType:
    return WalletType(
        id=str(row["id"]),
        currency=row["currency"],
        status=row["status"],
        created_at=_moment(row["created_at"]),
        balance=balance_type(row["balance"]),
    )


def charge_type(row: dict[str, Any]) -> ChargeType:
    return ChargeType(**row)


def entry_type(row: dict[str, Any]) -> EntryType:
    counterparty = row.get("counterparty_id")
    return EntryType(
        id=str(row["id"]),
        kind=row["kind"],
        direction=row["direction"],
        method=row["method"],
        payment_method=row["payment_method"],
        payment_method_name=row["payment_method_name"],
        network=row["network"],
        network_name=row["network_name"],
        amount=row["amount"],
        signed_amount=row["signed_amount"],
        currency=row["currency"],
        wallet_currency=row["wallet_currency"],
        gross_amount=row["gross_amount"],
        fee_total=row["fee_total"],
        net_amount=row["net_amount"],
        charges=[charge_type(charge) for charge in row["charges"]],
        converted=row["converted"],
        exchange_rate=row["exchange_rate"],
        destination=row["destination"],
        status=row["status"],
        settled=row["settled"],
        counts_towards_balance=row["counts_towards_balance"],
        approval=row["approval"],
        awaiting_approval=row["awaiting_approval"],
        reviewed_at=_moment(row["reviewed_at"]),
        review_note=row["review_note"],
        reference=row["reference"],
        external_reference=row["external_reference"],
        description=row["description"],
        metadata=row["metadata"],
        counterparty_id=str(counterparty) if counterparty else None,
        archived=row["archived"],
        created_at=_moment(row["created_at"]),
        settled_at=_moment(row["settled_at"]),
    )


def entry_page_type(
    rows: list[dict[str, Any]], total: int, limit: int, offset: int
) -> EntryPageType:
    return EntryPageType(
        entries=[entry_type(row) for row in rows], total=total, limit=limit, offset=offset
    )


def checkpoint_type(row: dict[str, Any]) -> CheckpointType:
    return CheckpointType(
        id=str(row["id"]),
        sequence=row["sequence"],
        balance=row["balance"],
        credited=row["credited"],
        debited=row["debited"],
        entry_count=row["entry_count"],
        created_at=_moment(row["created_at"]),
    )


def method_type(row: dict[str, Any]) -> MethodType:
    return MethodType(
        code=row["code"],
        name=row["name"],
        rail=row["rail"],
        family=row["family"],
        description=row["description"],
        instructions=row["instructions"],
        icon=row["icon"],
        directions=row["directions"],
        settles_immediately=row["settles_immediately"],
        reversible=row["reversible"],
        requires_approval=row["requires_approval"],
        needs_network=row["needs_network"],
        needs_destination=row["needs_destination"],
        currencies=[
            CurrencyType(
                currency=asset["currency"],
                min_amount=asset["min_amount"],
                max_amount=asset["max_amount"],
                decimals=asset["decimals"],
                networks=[NetworkType(**network) for network in asset["networks"]],
            )
            for asset in row["currencies"]
        ],
        fees=[FeeType(**fee) for fee in row["fees"]],
    )


def exchange_type(row: dict[str, Any]) -> ExchangeType:
    return ExchangeType(**row)


def rate_type(row: dict[str, Any]) -> RateType:
    return RateType(
        base=row["base"],
        quote=row["quote"],
        rate=row["rate"],
        margin_percent=row["margin_percent"],
        source=row["source"],
        effective_from=_moment(row["effective_from"]),
    )


def quote_type(row: dict[str, Any]) -> QuoteType:
    return QuoteType(
        method=row["method"],
        method_name=row["method_name"],
        rail=row["rail"],
        direction=row["direction"],
        currency=row["currency"],
        wallet_currency=row["wallet_currency"],
        network=row["network"],
        gross=row["gross"],
        charges=[charge_type(charge) for charge in row["charges"]],
        fee_total=row["fee_total"],
        absorbed_total=row["absorbed_total"],
        net=row["net"],
        wallet_amount=row["wallet_amount"],
        settlement_amount=row["settlement_amount"],
        converted=row["converted"],
        exchange=exchange_type(row["exchange"]),
        requires_approval=row["requires_approval"],
        settles_immediately=row["settles_immediately"],
    )
