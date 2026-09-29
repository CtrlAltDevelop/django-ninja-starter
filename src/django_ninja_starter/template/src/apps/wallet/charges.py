"""What a movement costs, what it converts at, and what actually reaches the wallet.

One module, one job: turn "deposit 100 EUR through `stripe-card`" into every
number the rest of the app and the customer need -- the commission, the tax on
the commission, the network fee, the rate, and the amount that ends up moving the
balance. It touches the configuration and the money, and nothing else: no writes,
no locks, no wallet.

That isolation is the point, because it means the price a customer is *quoted*
and the price they are *charged* come out of the same function. A quote endpoint
that re-implements the arithmetic is a quote endpoint that will one day differ
from the charge by a cent, and no amount of apologising makes that not a bug.

**The rule for fees is one sentence: charges come out of the amount the customer
named.** A deposit of 100 with 3 in fees credits 97. A withdrawal of 100 with 3
in fees debits 100 and pays out 97. Uniform in both directions, so a limit means
the same thing whichever way money is going, and the number the customer typed is
the number they recognise on the record.

**The rule for the spread is also one sentence: it is always taken against the
customer.** Money coming in converts at slightly less than the rate, money going
out at slightly more. Which is what a spread is; an implementation that applied
it by the shape of the pair would hand money away half the time.

**Transfers between two wallets here cost nothing.** Not "default to zero" --
nothing, enforced. The money never leaves, so there is no cost to pass on, and
:meth:`MethodFee.clean` will not even store a row that says otherwise.
"""

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from django.utils import timezone

from apps.wallet.catalog import (
    Basis,
    ChargeKind,
    ExchangeRate,
    MethodCurrency,
    MethodFee,
    MethodNetwork,
    PaymentMethod,
)
from apps.wallet.errors import (
    CurrencyNotAllowed,
    InvalidAmount,
    MethodNotAllowed,
    NetworkRequired,
    NoExchangeRate,
)
from apps.wallet.methods import Direction
from apps.wallet.money import HUNDRED, ZERO, clamp, money, percentage

ONE = Decimal("1")


@dataclass(frozen=True)
class Charge:
    """One component of the cost, as the customer will see it on the record."""

    kind: str
    label: str
    percent: Decimal
    fixed: Decimal
    amount: Decimal
    absorbed: bool
    fee_id: Any = None

    def payload(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "label": self.label,
            "percent": self.percent,
            "amount": self.amount,
            "absorbed": self.absorbed,
        }


@dataclass(frozen=True)
class Conversion:
    """One currency turned into another, and the arithmetic that did it.

    Kept even when nothing was converted -- ``rate`` of one, same currency both
    ends -- so every caller reads the same shape instead of branching on ``None``.
    """

    base: str
    quote: str
    rate: Decimal
    margin_percent: Decimal
    effective_rate: Decimal
    amount: Decimal
    converted: Decimal
    rate_id: Any = None
    inverted: bool = False

    @property
    def is_identity(self) -> bool:
        return self.base == self.quote

    def payload(self) -> dict[str, Any]:
        return {
            "base": self.base,
            "quote": self.quote,
            "rate": self.rate,
            "margin_percent": self.margin_percent,
            "effective_rate": self.effective_rate,
            "amount": self.amount,
            "converted": self.converted,
            "inverted": self.inverted,
        }


def identity(currency: str, amount: Decimal) -> Conversion:
    """The conversion from a currency to itself, which every deployment has."""
    return Conversion(
        base=currency,
        quote=currency,
        rate=ONE,
        margin_percent=Decimal("0"),
        effective_rate=ONE,
        amount=money(amount),
        converted=money(amount),
    )


def live_rate(base: str, quote: str, *, now: Any = None) -> tuple[ExchangeRate, bool] | None:
    """The rate in force for a pair, and whether it had to be read backwards.

    A deployment quoting EUR/USD should not also have to maintain USD/EUR: the
    second is the reciprocal of the first and keeping both in step by hand is a
    job nobody does correctly for long. So a direct rate wins, and an inverse one
    is used when there is no direct one -- carrying its margin with it, because
    the spread belongs to the pair however it is read.
    """
    base, quote = base.upper(), quote.upper()
    moment = now or timezone.now()
    direct = ExchangeRate.objects.live(now=moment).filter(base=base, quote=quote).first()
    if direct is not None:
        return direct, False
    inverse = ExchangeRate.objects.live(now=moment).filter(base=quote, quote=base).first()
    if inverse is not None:
        return inverse, True
    return None


def convert(
    amount: Decimal, base: str, quote: str, *, direction: str, now: Any = None
) -> Conversion:
    """Turn ``amount`` from one currency into another at the rate in force.

    ``direction`` is which way the *wallet* moves, and it decides which side of
    the spread the customer lands on: a credit converts at a shade under the rate,
    a debit at a shade over. Both are the deployment keeping the margin.
    """
    base, quote = base.upper(), quote.upper()
    if base == quote:
        return identity(base, amount)

    found = live_rate(base, quote, now=now)
    if found is None:
        raise NoExchangeRate(
            f"No live rate for {base}/{quote}. Add one before converting between them."
        )
    row, inverted = found
    raw = (ONE / row.rate) if inverted else Decimal(row.rate)
    margin = Decimal(row.margin_percent or 0)
    # The spread, always against whoever is on the other side of it: less comes
    # in than the rate says, more goes out than it says.
    shift = (
        (ONE - margin / HUNDRED) if direction == str(Direction.CREDIT) else (ONE + margin / HUNDRED)
    )
    effective = raw * shift
    return Conversion(
        base=base,
        quote=quote,
        rate=raw,
        margin_percent=margin,
        effective_rate=effective,
        amount=money(amount),
        converted=money(Decimal(amount) * effective),
        rate_id=row.pk,
        inverted=inverted,
    )


def applicable_fees(method: PaymentMethod, *, direction: str, currency: str) -> list[MethodFee]:
    """The method's live fees for one direction and currency, in the order they apply.

    Order is load-bearing rather than cosmetic: a tax whose basis is "the charges
    before it" reads whatever has been worked out so far, so a tax positioned
    above the commission it taxes would be a tax on nothing.
    """
    if not method.chargeable:
        return []
    return [
        fee
        for fee in method.fees.all().order_by("position", "kind")
        if fee.covers(direction, currency)
    ]


def compute_charges(
    method: PaymentMethod | None,
    *,
    direction: str,
    currency: str,
    amount: Decimal,
    network: MethodNetwork | None = None,
) -> list[Charge]:
    """Every charge on one movement, worked out in the order they are configured.

    Rounded once each, at the moment each charge becomes a number the customer
    will see on its own line. A charge is shown to them, so it has to be a real
    amount rather than a running fraction -- and the total is the sum of the lines
    rather than a separately rounded figure, so the record adds up.
    """
    if method is None or not method.chargeable:
        return []

    charges: list[Charge] = []
    running = ZERO
    for fee in applicable_fees(method, direction=direction, currency=currency):
        basis = running if fee.basis == str(Basis.CHARGES) else Decimal(amount)
        raw = percentage(basis, fee.percent) + Decimal(fee.fixed or 0)
        settled = money(clamp(raw, smallest=fee.minimum or None, largest=fee.maximum or None))
        if settled <= ZERO:
            continue
        charges.append(
            Charge(
                kind=fee.kind,
                label=fee.display_label,
                percent=Decimal(fee.percent or 0),
                fixed=Decimal(fee.fixed or 0),
                amount=settled,
                absorbed=fee.absorbed,
                fee_id=fee.pk,
            )
        )
        if not fee.absorbed:
            running += settled

    # The chain's own fee is charged from the network rather than from a fee row,
    # because it belongs to the chain: the same asset costs one thing on Tron and
    # quite another on Ethereum, and duplicating that per method would drift.
    if network is not None and network.network_fee and direction == str(Direction.DEBIT):
        charges.append(
            Charge(
                kind=str(ChargeKind.NETWORK),
                label=f"{network.name} network fee",
                percent=Decimal("0"),
                fixed=Decimal(network.network_fee),
                amount=money(network.network_fee),
                absorbed=False,
            )
        )
    return charges


@dataclass(frozen=True)
class Quote:
    """Every number one movement produces, worked out once and used everywhere.

    What the quote endpoint answers with, and what the recording path writes down.
    The same object either way, which is the only way the two can be guaranteed to
    agree.
    """

    direction: str
    currency: str
    wallet_currency: str
    gross: Decimal
    charges: tuple[Charge, ...]
    conversion: Conversion
    wallet_amount: Decimal
    settlement_amount: Decimal
    method_code: str = ""
    method_name: str = ""
    rail: str = ""
    requires_approval: bool = False
    settles_immediately: bool = False
    network_code: str = ""
    method: Any = field(default=None, repr=False, compare=False)
    network: Any = field(default=None, repr=False, compare=False)

    @property
    def fee_total(self) -> Decimal:
        """What the customer actually pays for the movement."""
        return money(sum((charge.amount for charge in self.charges if not charge.absorbed), ZERO))

    @property
    def absorbed_total(self) -> Decimal:
        """What this deployment paid out of its own margin, recorded for the reporting."""
        return money(sum((charge.amount for charge in self.charges if charge.absorbed), ZERO))

    @property
    def net(self) -> Decimal:
        """The amount after charges, in the currency the movement was named in."""
        return money(self.gross - self.fee_total)

    @property
    def converted(self) -> bool:
        return not self.conversion.is_identity

    def payload(self) -> dict[str, Any]:
        return {
            "method": self.method_code,
            "method_name": self.method_name,
            "rail": self.rail,
            "direction": self.direction,
            "currency": self.currency,
            "wallet_currency": self.wallet_currency,
            "network": self.network_code,
            "gross": self.gross,
            "charges": [charge.payload() for charge in self.charges],
            "fee_total": self.fee_total,
            "absorbed_total": self.absorbed_total,
            "net": self.net,
            "wallet_amount": self.wallet_amount,
            "settlement_amount": self.settlement_amount,
            "converted": self.converted,
            "exchange": self.conversion.payload(),
            "requires_approval": self.requires_approval,
            "settles_immediately": self.settles_immediately,
        }


def resolve_currency(method: PaymentMethod, currency: str) -> MethodCurrency:
    """The method's row for a currency, or a refusal naming what it does take."""
    row = method.currency(currency)
    if row is None:
        takes = sorted(method.currencies.filter(is_enabled=True).values_list("currency", flat=True))
        raise CurrencyNotAllowed(
            f"{method.name} does not take {currency.upper()}."
            + (f" It takes: {', '.join(takes)}." if takes else " It takes nothing yet.")
        )
    return row


def resolve_network(
    method: PaymentMethod, asset: MethodCurrency, code: str
) -> MethodNetwork | None:
    """The chain a crypto movement travels on, refused rather than guessed at.

    Never defaulted, even when the asset has exactly one chain configured today.
    A default that is right once becomes wrong the moment a second chain is added,
    and the failure mode is a customer paying to an address on the wrong network.
    """
    if not method.needs_network:
        return None
    if not code:
        available = sorted(asset.networks.filter(is_enabled=True).values_list("code", flat=True))
        raise NetworkRequired(
            f"{asset.currency} moves on more than one chain, so the network has to be named."
            + (f" Available: {', '.join(available)}." if available else "")
        )
    network = asset.networks.filter(code=code.lower(), is_enabled=True).first()
    if network is None:
        raise NetworkRequired(f"{asset.currency} is not carried on {code!r} here.")
    return network


def check_bounds(amount: Decimal, *, smallest: Decimal, largest: Decimal, currency: str) -> None:
    """The configured floor and ceiling, where zero means "no bound"."""
    if amount is None or amount <= ZERO:
        raise InvalidAmount("An amount has to be more than zero.")
    if smallest and amount < smallest:
        raise InvalidAmount(f"The smallest allowed here is {smallest} {currency}.")
    if largest and amount > largest:
        raise InvalidAmount(f"The largest allowed here is {largest} {currency}.")


def quote_movement(
    method: PaymentMethod,
    *,
    direction: str,
    amount: Decimal,
    currency: str,
    wallet_currency: str,
    network_code: str = "",
    now: Any = None,
) -> Quote:
    """Price one movement completely, without touching a wallet or writing anything.

    The single path to a price. Called by the quote endpoint so a customer can see
    the cost before committing, and called again by the recording path so what is
    written down is what they were shown.

    Limits are checked against ``amount`` -- the figure the customer named, before
    charges -- because that is the figure they typed into the box, and a minimum
    that silently means "after fees" rejects deposits for reasons nobody can see.
    """
    if not method.carries(direction):
        way = "take money in" if direction == str(Direction.CREDIT) else "pay money out"
        raise MethodNotAllowed(f"{method.name} does not {way}.")

    asset = resolve_currency(method, currency)
    network = resolve_network(method, asset, network_code)
    smallest, largest = (network or asset).bounds()
    check_bounds(amount, smallest=smallest, largest=largest, currency=asset.currency)

    charges = compute_charges(
        method,
        direction=direction,
        currency=asset.currency,
        amount=Decimal(amount),
        network=network,
    )
    payable = money(sum((charge.amount for charge in charges if not charge.absorbed), ZERO))
    net = money(Decimal(amount) - payable)
    if net <= ZERO:
        raise InvalidAmount(
            f"The charges on this movement come to {payable} {asset.currency}, which is "
            f"the whole of it. Ask for more than the fees cost."
        )

    # A deposit credits what survived the charges; a withdrawal debits the whole
    # amount and pays out what survived. One rule, read from both ends.
    moving = net if direction == str(Direction.CREDIT) else Decimal(amount)
    conversion = convert(moving, asset.currency, wallet_currency, direction=direction, now=now)

    return Quote(
        direction=direction,
        currency=asset.currency,
        wallet_currency=wallet_currency.upper(),
        gross=money(amount),
        charges=tuple(charges),
        conversion=conversion,
        wallet_amount=conversion.converted,
        settlement_amount=money(amount) if direction == str(Direction.CREDIT) else net,
        method_code=method.code,
        method_name=method.name,
        rail=method.rail,
        requires_approval=method.requires_approval,
        settles_immediately=method.settles_immediately,
        network_code=network.code if network else "",
        method=method,
        network=network,
    )


def free_quote(*, direction: str, amount: Decimal, currency: str, rail: str) -> Quote:
    """A movement with no method, no charges and no conversion.

    What an internal transfer, a system adjustment, a fee or a bonus gets. There
    is no configured method behind any of them and nothing to price, so this
    builds the same object the priced path does with all the costs at zero --
    letting the recording path stay one path instead of two.
    """
    return Quote(
        direction=direction,
        currency=currency.upper(),
        wallet_currency=currency.upper(),
        gross=money(amount),
        charges=(),
        conversion=identity(currency.upper(), amount),
        wallet_amount=money(amount),
        settlement_amount=money(amount),
        rail=rail,
        requires_approval=False,
        settles_immediately=True,
    )
