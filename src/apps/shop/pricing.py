"""What a thing actually costs right now, and which campaign made it that.

The product's ``price`` is what the shop charges when nothing is going on. This
module is the other half: the campaigns that are running, which of them reach a
given product, and what the shopper therefore pays.

Two rules decide everything here.

**The best discount wins, and only one applies.** Stacking is the alternative,
and it is how a shop accidentally sells at a negative price the weekend two
campaigns overlap. Whichever single campaign saves the shopper the most is the
one that applies; ``priority`` breaks a tie, so a shop that wants a specific
campaign to be the one that shows can say so.

**Nothing is stored.** There is no ``sale_price`` column and no cached
computation, because the input that changes most often is the clock. A price
that was computed at write time is a price that is wrong from the moment the
campaign ends, and finding out costs a customer.

The cost of that is a query for the running campaigns, which is why every
function here takes an optional pre-loaded list: a listing of forty products
loads them once and hands the same list to all forty.
"""

from dataclasses import dataclass
from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal
from typing import TYPE_CHECKING, Any
from uuid import UUID

from apps.shop import options
from apps.shop.models import Discount, DiscountKind

if TYPE_CHECKING:  # pragma: no cover - import cycle only a checker walks
    from apps.shop.models import Product, ProductOffer, ProductVariant

CENTS = Decimal("0.01")
ZERO = Decimal("0.00")


@dataclass(frozen=True)
class AppliedDiscount:
    """The campaign a price is under, in the shape a storefront prints it."""

    id: str
    name: str
    kind: str
    value: Decimal
    amount_off: Decimal
    percent_off: int
    ends_at: datetime | None

    def payload(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "kind": self.kind,
            "value": self.value,
            "amount_off": self.amount_off,
            "percent_off": self.percent_off,
            "ends_at": self.ends_at,
        }


@dataclass(frozen=True)
class Price:
    """What one thing costs, and what it would have cost."""

    amount: Decimal
    base_amount: Decimal
    currency: str
    discount: AppliedDiscount | None

    @property
    def is_discounted(self) -> bool:
        return self.discount is not None

    def payload(self) -> dict[str, Any]:
        return {
            "amount": self.amount,
            "base_amount": self.base_amount,
            "currency": self.currency,
            "is_discounted": self.is_discounted,
            "discount": self.discount.payload() if self.discount else None,
        }


def to_cents(amount: Decimal) -> Decimal:
    """Money, to the cent, rounded the way a till rounds.

    Public because the checkout needs it too: multiplying and dividing
    ``Decimal`` money produces as many places as the arithmetic implies, and a
    column that holds two refuses the rest rather than rounding it for you.
    """
    return amount.quantize(CENTS, rounding=ROUND_HALF_UP)


def running_discounts(at: datetime | None = None) -> list[Discount]:
    """Every campaign that is on right now, with what it applies to already loaded.

    Loaded once and passed around rather than queried per product: a listing
    asks this question forty times, and it has the same answer forty times.
    """
    return list(Discount.objects.running(at).prefetch_related("products", "categories__children"))


def reach(discounts: list[Discount]) -> dict[UUID, set[UUID]]:
    """Each campaign's category ids, walked down the tree once rather than per product."""
    return {discount.pk: discount.category_branch_ids() for discount in discounts}


def base_price(
    product: "Product",
    variant: "ProductVariant | None" = None,
    offer: "ProductOffer | None" = None,
) -> Decimal:
    """What is charged for this before any campaign touches it.

    Most specific wins: another seller's offer, then the variant's own override,
    then the product's price -- which is the primary seller's.
    """
    if offer is not None:
        return Decimal(offer.price)
    if variant is not None and variant.price is not None:
        return Decimal(variant.price)
    return Decimal(product.price)


def _saving(discount: Discount, amount: Decimal) -> Decimal:
    """How much this campaign takes off that amount, capped and never below zero."""
    if discount.kind == DiscountKind.PERCENT:
        off = to_cents(amount * Decimal(discount.value) / Decimal("100"))
        if discount.max_amount is not None:
            off = min(off, Decimal(discount.max_amount))
    else:
        off = Decimal(discount.value)
    return min(max(off, ZERO), amount)


def best_discount(
    product: "Product",
    amount: Decimal,
    discounts: list[Discount],
    branches: dict[UUID, set[UUID]] | None = None,
) -> tuple[Discount | None, Decimal]:
    """The single campaign that saves the shopper most, and what it saves.

    Ties go to the higher ``priority`` -- which is the whole reason that column
    exists, since two campaigns worth the same amount are otherwise settled by
    whichever the database happened to return first.
    """
    reached = branches if branches is not None else reach(discounts)
    winner: Discount | None = None
    best = ZERO
    for discount in discounts:
        if not discount.covers(product, branch_ids=reached.get(discount.pk, set())):
            continue
        saving = _saving(discount, amount)
        if saving <= ZERO:
            continue
        if winner is None or (saving, discount.priority) > (best, winner.priority):
            winner, best = discount, saving
    return winner, best


def price_of(
    product: "Product",
    variant: "ProductVariant | None" = None,
    discounts: list[Discount] | None = None,
    branches: dict[UUID, set[UUID]] | None = None,
    offer: "ProductOffer | None" = None,
) -> Price:
    """What a shopper pays for this product, in this variant, from this seller.

    A campaign reaches a seller's offer the same way it reaches the shop's own
    price. That is the only defensible reading of "20% off everything in
    Kitchen": a campaign is the shop's decision about a category, and an offer
    that quietly escaped it would be the cheapest listing on the page one minute
    and the dearest the next.
    """
    campaigns = running_discounts() if discounts is None else discounts
    base = to_cents(base_price(product, variant, offer))
    discount, saving = best_discount(product, base, campaigns, branches)
    if discount is None:
        return Price(amount=base, base_amount=base, currency=options.currency(), discount=None)
    final = to_cents(base - saving)
    percent = int((saving / base * 100).to_integral_value(rounding=ROUND_HALF_UP)) if base else 0
    return Price(
        amount=final,
        base_amount=base,
        currency=options.currency(),
        discount=AppliedDiscount(
            id=str(discount.pk),
            name=discount.name,
            kind=str(discount.kind),
            value=Decimal(discount.value),
            amount_off=saving,
            percent_off=percent,
            ends_at=discount.ends_at,
        ),
    )


def shelf(
    product: "Product",
    variant: "ProductVariant | None" = None,
    offer: "ProductOffer | None" = None,
) -> int:
    """How many of one thing one seller has, read off the row that owns the count.

    Three tables carry a ``stock`` column and they are not alternatives: a
    product's own row is the primary seller's shelf, a variant's is that size in
    that colour, and an offer's is somebody else's warehouse. Most specific
    wins, exactly as :func:`base_price` picks which price applies -- and every
    question about availability in this app is asked through here, so the three
    of them cannot drift apart into three slightly different rules.
    """
    if offer is not None:
        return offer.stock
    if variant is not None:
        return variant.stock
    return product.available_stock


def can_fill(
    product: "Product",
    variant: "ProductVariant | None" = None,
    offer: "ProductOffer | None" = None,
    quantity: int = 1,
) -> bool:
    """Whether that one seller could fill an order for that many, right now.

    A product nobody counts, and a product that takes backorders, can always
    fill one: the shop has said so on the product row, and the answer is the
    same whichever shelf the line names.
    """
    if not product.track_inventory or product.allow_backorder:
        return True
    return shelf(product, variant, offer) >= quantity


def sellable_offers(
    product: "Product", variant: "ProductVariant | None" = None, *, any_variant: bool = False
) -> list[Any]:
    """The other sellers who could fill an order for this, cheapest first.

    Filtered in Python over the prefetched rows rather than queried, because a
    product page and a listing of forty have already loaded them: asking the
    database again per product is how a catalogue page becomes eighty queries.

    ``any_variant`` drops the variant filter rather than matching it, which is
    what a product *page* wants: it is showing every size at once, so it needs
    everybody selling any of them, not just the sellers of the product row.
    """
    wanted = variant.pk if variant else None
    return sorted(
        (
            offer
            for offer in product.offers.all()
            if offer.is_active
            and offer.seller.is_active
            and (any_variant or offer.variant_id == wanted)
            and can_fill(product, variant, offer)
        ),
        key=lambda offer: (Decimal(offer.price), offer.lead_time_days),
    )


def total_stock(product: "Product", variant: "ProductVariant | None" = None) -> int:
    """Every unit of this one thing anybody could sell, across all of its sellers.

    The number a product page prints next to a size. A shopper does not care
    which warehouse it is in, and a page that showed only the primary seller's
    count would say "out of stock" over a size three other shops have.
    """
    own = shelf(product, variant) if own_row_is_sellable(product, variant) else 0
    return own + sum(offer.stock for offer in sellable_offers(product, variant))


def is_available(
    product: "Product", variant: "ProductVariant | None" = None, quantity: int = 1
) -> bool:
    """Whether *anybody* -- the shop or another seller -- could fill an order for this."""
    if can_fill(product, variant, None, quantity):
        return True
    return any(
        can_fill(product, variant, offer, quantity) for offer in sellable_offers(product, variant)
    )


def buy_box(
    product: "Product",
    variant: "ProductVariant | None" = None,
    discounts: list[Discount] | None = None,
    branches: dict[UUID, set[UUID]] | None = None,
) -> tuple[Any | None, Price]:
    """Who a shopper buys from by default, and what they pay.

    Returns ``(offer, price)`` where an offer of ``None`` means the product's own
    row -- the primary seller's price and stock. The cheapest *after* campaigns
    wins, because that is the number on the page; ties go to whoever dispatches
    sooner.

    A shop with no offers gets ``(None, ...)`` without touching the offer table,
    which is what keeps the single-vendor case exactly as cheap as it was before
    there was a marketplace.
    """
    own = price_of(product, variant, discounts, branches)
    candidates: list[tuple[Any | None, Price]] = []
    if own_row_is_sellable(product, variant):
        candidates.append((None, own))
    for offer in sellable_offers(product, variant):
        candidates.append((offer, price_of(product, variant, discounts, branches, offer)))
    if not candidates:
        # Nothing is buyable. The price still has to be printed, so it is the
        # product's own -- a page saying "out of stock" needs a number under it.
        return None, own
    return min(candidates, key=lambda row: (row[1].amount, _lead_time(row[0])))


def own_row_is_sellable(product: "Product", variant: "ProductVariant | None" = None) -> bool:
    """Whether the primary seller could fill an order for one of these."""
    return can_fill(product, variant)


def _lead_time(offer: Any | None) -> int:
    """The product's own row dispatches today; an offer says when it does."""
    return 0 if offer is None else offer.lead_time_days


def discounted_ids(products: list["Product"], discounts: list[Discount] | None = None) -> set[UUID]:
    """Which of these products a campaign is currently reaching.

    What "on sale" means as a filter. Computed rather than stored for the same
    reason the price is: the answer changes when a clock passes a timestamp, and
    nothing wrote a row at that moment.
    """
    campaigns = running_discounts() if discounts is None else discounts
    if not campaigns:
        return set()
    branches = reach(campaigns)
    return {
        product.pk
        for product in products
        if best_discount(product, to_cents(Decimal(product.price)), campaigns, branches)[0]
    }
