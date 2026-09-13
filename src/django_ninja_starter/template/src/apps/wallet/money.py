"""Money, percentages and rates: the field widths, and the rounding that goes with them.

Three quantities live in this app and they want different precision, so each one
gets a declaration here rather than a ``DecimalField(...)`` guessed at every
model that needs it.

**Amounts** are eighteen digits with four after the point. That holds a realistic
balance in any currency -- including the minor units a crypto rail quotes --
without inviting a float anywhere near money.

**Percentages** are a commission of ``2.5``, not a multiplier of ``0.025``. An
operator filling in a fee form types the number on the price list, and a field
that silently means something a hundred times different is a mistake nobody
catches until a customer is charged for it. Four decimals, because tax rates
really are quoted like ``8.8750``.

**Rates** are wider than amounts on purpose: one satoshi in euros and one yen in
bitcoin are both real numbers a deployment may have to store, and they are
sixteen orders of magnitude apart. Twelve decimals covers the pair.

Rounding is ``ROUND_HALF_UP`` and it is applied at exactly one point in a
calculation -- the end. Rounding each intermediate charge and then summing them
drifts away from the total the customer was quoted, and "the fee is a cent
different from the quote" is a support ticket that costs more than the cent.
"""

from decimal import ROUND_HALF_UP, Decimal
from typing import Any

#: Every amount in this app: a balance, an entry, a charge, a limit.
MONEY: dict[str, Any] = {"max_digits": 18, "decimal_places": 4}

#: A percentage as an operator writes it: ``2.5`` means two and a half percent.
PERCENT: dict[str, Any] = {"max_digits": 7, "decimal_places": 4}

#: An exchange rate. Wide enough for both ends of any pair a deployment holds.
RATE: dict[str, Any] = {"max_digits": 28, "decimal_places": 12}

ZERO = Decimal("0.0000")
HUNDRED = Decimal("100")

#: What an amount is rounded to, once, at the end of a calculation.
UNIT = Decimal("0.0001")


def money(value: Decimal | int | str | None) -> Decimal:
    """One amount, rounded to the precision amounts are stored at.

    Called at the end of a calculation and not in the middle of one. ``None``
    becomes zero, because the aggregate over an empty queryset is ``None`` and
    every caller of this would otherwise write the same guard.
    """
    if value is None:
        return ZERO
    return Decimal(value).quantize(UNIT, rounding=ROUND_HALF_UP)


def percentage(amount: Decimal, percent: Decimal | None) -> Decimal:
    """``percent`` percent of ``amount``, unrounded.

    Unrounded deliberately: this is an intermediate, and the sum of several of
    them is rounded once by whoever adds them up.
    """
    if not percent:
        return Decimal("0")
    return Decimal(amount) * Decimal(percent) / HUNDRED


def clamp(value: Decimal, *, smallest: Decimal | None, largest: Decimal | None) -> Decimal:
    """``value`` held between two bounds, where zero or ``None`` means "no bound".

    Zero as "unset" is the convention the whole app uses for limits: a deployment
    that has not thought about a ceiling should get no ceiling, rather than one
    this app invented on its behalf.
    """
    if smallest and value < smallest:
        return Decimal(smallest)
    if largest and value > largest:
        return Decimal(largest)
    return value


def written(amount: Decimal | int | str | None, currency: str = "", *, decimals: int = 2) -> str:
    """One amount, written the way a person writes money rather than the way it
    is stored.

    Amounts are stored at four decimals so that a rail quoting minor units has
    somewhere to put them, and every screen that printed one straight got
    ``1250.5000 USD`` -- four digits of storage precision presented as though
    they meant something. This trims the padding and keeps everything real:
    ``1250.50``, ``99.00``, and ``12.3456`` for the amount that genuinely has
    four decimals. Never fewer than ``decimals``, because ``1250.5`` reads as an
    amount somebody typed carelessly, and never rounded, because rounding money
    for display is how a total stops matching the rows above it.
    """
    value = money(amount)
    _, _, fraction = f"{value:f}".partition(".")
    kept = max(decimals, len(fraction.rstrip("0")))
    return f"{value:.{kept}f} {currency}".strip()
