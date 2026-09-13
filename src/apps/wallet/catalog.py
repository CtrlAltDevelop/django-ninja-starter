"""What an administrator configures: the ways to pay, what they cost, and the rates.

:mod:`apps.wallet.methods` says what a card *is*. This says which card processor
this deployment runs, that it takes euros and dollars but not yen, that it
charges 2.9% plus 30 cents, that the tax authority wants 20% of that commission,
and that a deposit through it is a request until somebody in the back office
confirms the money arrived.

All of which changes on a Tuesday afternoon because a contract was renegotiated.
None of it should need a release, so none of it is in code.

Five tables, each one narrower than the last:

:class:`PaymentMethod`
    One configured way to pay, pointing at a rail. Everything a client needs to
    render a choice, and everything this app needs to price one.

:class:`MethodCurrency`
    A currency that method takes, and what the limits are *in that currency*. A
    single ``min_amount`` across dollars and bitcoin is not a limit, it is a
    rounding error in one of them, so the bounds that matter live here.

:class:`MethodNetwork`
    For crypto: which chain an asset moves on. USDT on Ethereum and USDT on Tron
    are the same balance and two completely different things to pay to -- sending
    to the wrong one loses the money -- so they are separate rows with separate
    addresses, fees and confirmation counts.

:class:`MethodFee`
    One component of what a movement costs: a commission, a tax, a fixed cost, a
    network fee. Several per method, applied in order, each one recorded against
    the entry it was charged on.

:class:`ExchangeRate`
    What one currency is worth in another, with the spread this deployment keeps.

**Two rails can never carry a fee**, whatever a row says: a transfer between two
wallets in this app and a correction made by the app itself both move money that
never leaves the building. There is no cost to pass on, so there is nothing to
charge, and :meth:`MethodFee.clean` refuses the row rather than letting an
administrator quietly tax an internal transfer. See :attr:`MethodSpec.chargeable`.
"""

import uuid
from decimal import Decimal
from typing import TYPE_CHECKING, Any

from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.utils import timezone

from apps.wallet.methods import Direction, Method, UnknownMethod
from apps.wallet.methods import spec as rail_spec
from apps.wallet.money import MONEY, PERCENT, RATE, ZERO

if TYPE_CHECKING:
    from apps.wallet.methods import MethodSpec


class Applies(models.TextChoices):
    """Which direction a rule is about.

    A fee is rarely symmetric -- taking money in costs a percentage, paying it
    out costs a flat amount -- so every rule says which way it is about, and
    ``BOTH`` is available for the ones that genuinely are.
    """

    CREDIT = "credit", "Deposits"
    DEBIT = "debit", "Withdrawals"
    BOTH = "both", "Both"

    @staticmethod
    def covers(applies: str, direction: str) -> bool:
        return applies == str(Applies.BOTH) or applies == direction


class ChargeKind(models.TextChoices):
    """What a charge *is*, which is not the same as what it costs.

    Kept apart because they are owed to different people and reported
    differently: a commission is revenue, a tax is collected on somebody else's
    behalf, a network fee is paid to a miner and passed through, and a cost is
    what the processor billed. Collapsing them into one "fee" number is fine
    until somebody has to file a return.
    """

    COMMISSION = "commission", "Commission"
    """What this deployment keeps."""

    TAX = "tax", "Tax"
    """Collected for an authority. Often charged on the commission, not the amount."""

    COST = "cost", "Processing cost"
    """What the provider billed, passed through."""

    NETWORK = "network", "Network fee"
    """Paid to the chain. Belongs to the network, not to the method."""

    SERVICE = "service", "Service fee"
    """Anything else the deployment charges for and wants named separately."""


class Basis(models.TextChoices):
    """What a percentage is a percentage *of*.

    ``GROSS`` is the obvious one and the default. ``CHARGES`` exists because VAT
    on a payment commission is a real thing in most of Europe: the tax is a
    percentage of the fee, not of the amount, and a deployment that can only
    express the first will overcharge every customer it has.
    """

    GROSS = "gross", "The amount"
    CHARGES = "charges", "The charges before it"


class PaymentMethodQuerySet(models.QuerySet["PaymentMethod"]):
    def enabled(self) -> "PaymentMethodQuerySet":
        return self.filter(is_enabled=True)

    def for_direction(self, direction: str) -> "PaymentMethodQuerySet":
        field = "supports_deposit" if direction == str(Direction.CREDIT) else "supports_withdrawal"
        return self.filter(**{field: True})

    def usable(self) -> "PaymentMethodQuerySet":
        """Enabled rows whose rail this deployment also allows.

        Both gates, in one query. A row can be enabled in the admin and still be
        unusable because ``DJANGO_WALLET_METHODS`` does not list its rail, and a
        method the API advertises but refuses is worse than one it never showed.
        """
        from apps.wallet.methods import enabled_methods

        return self.enabled().filter(rail__in=list(enabled_methods()))


class PaymentMethod(models.Model):
    """One configured way to move money, and everything a client needs to offer it.

    The row an administrator creates and fills in. ``rail`` decides the physics --
    whether it settles at once, whether it can be charged back, what a customer
    has to supply -- and cannot be overridden here; everything else on this model
    is a commercial decision.

    ``requires_approval`` defaults to **on**, which is the conservative choice and
    deliberately the one you get without thinking about it: a movement created
    through this method is a *request* until an operator applies it. Turning it off
    means money moves on a customer's say-so, which is right for a card capture
    confirmed by a webhook and wrong for a cash deposit somebody claims to have
    made.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    code = models.SlugField(
        max_length=60,
        unique=True,
        help_text=(
            "What clients ask for this method by, such as `stripe-card` or "
            "`usdt-payout`. Stable: changing it breaks every client that stored it."
        ),
    )
    name = models.CharField(max_length=120, help_text="What a customer sees in a list.")
    rail = models.CharField(
        max_length=24,
        choices=Method.choices,
        help_text=(
            "The kind of thing this is. Decides settlement, reversibility and what "
            "the customer has to supply -- none of which this form can override."
        ),
    )
    description = models.TextField(
        blank=True, help_text="A sentence for the customer choosing between methods."
    )
    instructions = models.TextField(
        blank=True,
        help_text=(
            "What the customer has to actually do -- the counter to visit, the "
            "reference to quote. Shown after they pick this method."
        ),
    )
    icon = models.CharField(
        max_length=120, blank=True, help_text="An icon name or URL, for whatever renders the list."
    )

    is_enabled = models.BooleanField(
        default=False,
        help_text=(
            "Off until somebody has filled in the currencies and the fees. A method "
            "published half configured quotes the wrong price."
        ),
    )
    supports_deposit = models.BooleanField(default=True, help_text="Money can arrive this way.")
    supports_withdrawal = models.BooleanField(default=False, help_text="Money can leave this way.")
    requires_approval = models.BooleanField(
        default=True,
        help_text=(
            "On: a movement is a request an operator has to apply before it can "
            "settle. Off: the rail's own confirmation is enough."
        ),
    )

    min_amount = models.DecimalField(
        **MONEY,
        default=ZERO,
        validators=[MinValueValidator(ZERO)],
        help_text=(
            "A fallback floor for currencies that do not set their own. Zero means "
            "no floor. Real limits belong on the currency."
        ),
    )
    max_amount = models.DecimalField(
        **MONEY,
        default=ZERO,
        validators=[MinValueValidator(ZERO)],
        help_text="A fallback ceiling. Zero means none.",
    )

    position = models.IntegerField(default=0, help_text="Lower sorts first in the published list.")
    config = models.JSONField(
        default=dict,
        blank=True,
        help_text=(
            "Whatever the integration needs kept with the method: an account "
            "reference, a merchant id. Not secrets -- this is readable by anyone "
            "with admin access."
        ),
    )
    created_at = models.DateTimeField(default=timezone.now, editable=False)
    updated_at = models.DateTimeField(auto_now=True)

    objects = PaymentMethodQuerySet.as_manager()

    class Meta:
        ordering = ["position", "name"]
        indexes = [
            models.Index(fields=["is_enabled", "position"]),
            models.Index(fields=["rail"]),
        ]

    def __str__(self) -> str:
        return self.name

    @property
    def spec(self) -> "MethodSpec":
        """The rail's declaration: what this method physically is."""
        return rail_spec(self.rail)

    @property
    def family(self) -> str:
        return self.spec.family

    @property
    def settles_immediately(self) -> bool:
        return self.spec.settles_immediately

    @property
    def reversible(self) -> bool:
        return self.spec.reversible

    @property
    def needs_network(self) -> bool:
        return self.spec.needs_network

    @property
    def chargeable(self) -> bool:
        return self.spec.chargeable

    def carries(self, direction: str) -> bool:
        """Whether this configured method moves money that way, and may."""
        allowed = (
            self.supports_deposit
            if direction == str(Direction.CREDIT)
            else self.supports_withdrawal
        )
        return bool(allowed and self.spec.carries(direction))

    def currency(self, code: str) -> "MethodCurrency | None":
        """This method's row for one currency, if it takes it and it is switched on."""
        return self.currencies.filter(currency=code.upper(), is_enabled=True).first()

    def clean(self) -> None:
        """Refuse a configuration the rail cannot honour, while it is still a form.

        The checks that would otherwise be discovered by a customer: a method
        configured to pay out on a rail that only takes money in, and a method
        that does neither.
        """
        try:
            rail = self.spec
        except UnknownMethod as unknown:
            raise ValidationError({"rail": str(unknown)}) from None
        if self.supports_deposit and not rail.carries(str(Direction.CREDIT)):
            raise ValidationError({"supports_deposit": f"{rail.label} does not take money in."})
        if self.supports_withdrawal and not rail.carries(str(Direction.DEBIT)):
            raise ValidationError({"supports_withdrawal": f"{rail.label} does not pay money out."})
        if not (self.supports_deposit or self.supports_withdrawal):
            raise ValidationError("A method has to move money one way or the other.")
        if self.max_amount and self.min_amount and self.max_amount < self.min_amount:
            raise ValidationError(
                {"max_amount": "The ceiling is below the floor, so nothing can pass both."}
            )


class MethodCurrency(models.Model):
    """A currency one method takes, and the limits that actually apply.

    Limits live here rather than on the method because they are only meaningful
    next to a currency: a ten-unit minimum is a sensible card floor in dollars and
    a fortune in bitcoin. The method's own ``min_amount`` is a fallback for rows
    that leave these at zero.

    ``is_enabled`` is per currency on purpose. A processor suspending settlement
    in one currency is a Tuesday; taking the whole method offline for it would
    stop the other nine working.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    method = models.ForeignKey(PaymentMethod, on_delete=models.CASCADE, related_name="currencies")
    currency = models.CharField(
        max_length=12,
        help_text=(
            "ISO 4217 for money, or the ticker for an asset -- `USD`, `EUR`, `USDT`. "
            "Longer than three characters because crypto tickers are."
        ),
    )
    min_amount = models.DecimalField(
        **MONEY,
        default=ZERO,
        validators=[MinValueValidator(ZERO)],
        help_text=(
            "The smallest movement accepted in this currency. Zero falls back to the method's."
        ),
    )
    max_amount = models.DecimalField(
        **MONEY,
        default=ZERO,
        validators=[MinValueValidator(ZERO)],
        help_text="The largest. Zero falls back to the method's, and zero there means no ceiling.",
    )
    display_decimals = models.PositiveSmallIntegerField(
        default=2,
        validators=[MaxValueValidator(8)],
        help_text="How many decimals a client should show. Two for money, eight for a coin.",
    )
    is_enabled = models.BooleanField(default=True)
    position = models.IntegerField(default=0)

    class Meta:
        ordering = ["position", "currency"]
        verbose_name_plural = "method currencies"
        constraints = [
            models.UniqueConstraint(
                fields=["method", "currency"], name="wallet_method_currency_once"
            )
        ]

    def __str__(self) -> str:
        return f"{self.method.code} / {self.currency}"

    def save(self, *args: Any, **kwargs: Any) -> None:
        self.currency = self.currency.upper()
        super().save(*args, **kwargs)

    def bounds(self) -> tuple[Decimal, Decimal]:
        """The floor and ceiling that apply here, falling back to the method's.

        Zero means "not set" at both levels, so a deployment that has thought
        about neither gets no limit rather than one this app invented.
        """
        return (
            self.min_amount or self.method.min_amount or ZERO,
            self.max_amount or self.method.max_amount or ZERO,
        )

    def clean(self) -> None:
        if self.max_amount and self.min_amount and self.max_amount < self.min_amount:
            raise ValidationError({"max_amount": "The ceiling is below the floor."})


class MethodNetwork(models.Model):
    """One chain an asset moves on: the fee, the confirmations, and the address shape.

    A separate row per chain rather than a field on the currency, because that is
    what the money is like. USDT on Ethereum and USDT on Tron are one balance to
    the customer and two incompatible destinations to the network: paying to the
    wrong one does not fail, it loses the money. So the chain is picked
    explicitly, its address is validated against a pattern belonging to *that*
    chain, and its fee -- which differs by an order of magnitude between the two --
    is charged from here.

    ``confirmations`` is what the integration waits for before settling a deposit.
    This app stores it and does not act on it: what counts as final is a risk
    decision, and the process watching the chain is the one that makes it.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    asset = models.ForeignKey(MethodCurrency, on_delete=models.CASCADE, related_name="networks")
    code = models.SlugField(
        max_length=40, help_text="What the chain is called on an address label: `trc20`, `erc20`."
    )
    name = models.CharField(max_length=120, help_text="What a customer sees: `Tron (TRC20)`.")
    confirmations = models.PositiveIntegerField(
        default=1, help_text="How many blocks the integration waits for before settling a deposit."
    )
    address_pattern = models.CharField(
        max_length=255,
        blank=True,
        help_text=(
            "A regular expression an address on this chain matches, checked before a "
            "payout is recorded. Blank skips the check -- a wrong address here is "
            "money gone, so fill it in."
        ),
    )
    network_fee = models.DecimalField(
        **MONEY,
        default=ZERO,
        validators=[MinValueValidator(ZERO)],
        help_text=(
            "What the chain costs, in this asset, charged on withdrawals. Recorded "
            "against the entry as a `network` charge."
        ),
    )
    min_amount = models.DecimalField(
        **MONEY,
        default=ZERO,
        validators=[MinValueValidator(ZERO)],
        help_text="A floor for this chain alone. Zero falls back to the currency's.",
    )
    max_amount = models.DecimalField(**MONEY, default=ZERO, validators=[MinValueValidator(ZERO)])
    deposit_address = models.CharField(
        max_length=255,
        blank=True,
        help_text=(
            "Where customers send this asset on this chain, if the deployment uses one address."
        ),
    )
    explorer_url = models.CharField(
        max_length=255,
        blank=True,
        help_text="A URL with `{tx}` in it, so a client can link a transaction hash.",
    )
    is_enabled = models.BooleanField(default=True)
    position = models.IntegerField(default=0)

    class Meta:
        ordering = ["position", "name"]
        constraints = [
            models.UniqueConstraint(fields=["asset", "code"], name="wallet_method_network_once")
        ]

    def __str__(self) -> str:
        return f"{self.asset.currency} on {self.name}"

    def save(self, *args: Any, **kwargs: Any) -> None:
        """Normalised down, the way a currency is normalised up.

        Chain codes are looked up in lower case, so a row saved as ``TRC20`` is a
        chain nothing can reach: every payout naming it is refused with "not
        carried here", and the reason is invisible in the admin, where the row
        sits there looking configured.
        """
        self.code = self.code.lower()
        super().save(*args, **kwargs)

    def bounds(self) -> tuple[Decimal, Decimal]:
        smallest, largest = self.asset.bounds()
        return (self.min_amount or smallest, self.max_amount or largest)

    def accepts_address(self, address: str) -> bool:
        """Whether an address looks like one of this chain's. Blank pattern accepts anything."""
        if not self.address_pattern:
            return True
        import re

        try:
            return bool(re.fullmatch(self.address_pattern, address or ""))
        except re.error:
            # A pattern that does not compile is a configuration mistake, and
            # refusing every payout is the safe way to report it.
            return False


class MethodFee(models.Model):
    """One component of what a movement costs, and how it is worked out.

    Several per method, applied in ``position`` order, each one recorded against
    the entry as its own :class:`~apps.wallet.models.WalletCharge` row -- so a
    customer looking at a deposit sees "commission 2.90, tax 0.58" rather than a
    single unexplained 3.48.

    A fee is ``percent`` of its basis, plus ``fixed``, held between ``minimum``
    and ``maximum``. Any of the four may be left at zero, which is how the usual
    shapes are expressed: percentage-only, flat-only, or percentage with a floor.

    ``absorbed`` is the one that surprises people. An absorbed fee is calculated
    and recorded and *does not change what the customer gets* -- it is a cost this
    deployment is paying out of its own margin, written down so the reporting adds
    up. An unabsorbed fee is taken off a deposit, or added on top of a withdrawal.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    method = models.ForeignKey(PaymentMethod, on_delete=models.CASCADE, related_name="fees")
    currency = models.ForeignKey(
        MethodCurrency,
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="fees",
        help_text="Leave empty to charge this on every currency the method takes.",
    )
    kind = models.CharField(
        max_length=16, choices=ChargeKind.choices, default=ChargeKind.COMMISSION
    )
    label = models.CharField(
        max_length=120,
        blank=True,
        help_text="What the customer sees on the record. Defaults to the kind's own name.",
    )
    applies_to = models.CharField(
        max_length=8,
        choices=Applies.choices,
        default=str(Applies.BOTH),
        help_text="A fee is rarely the same in both directions.",
    )
    percent = models.DecimalField(
        **PERCENT,
        default=Decimal("0"),
        validators=[MinValueValidator(Decimal("0")), MaxValueValidator(Decimal("100"))],
        help_text="Written as an operator writes it: 2.9 means 2.9%, not 290%.",
    )
    fixed = models.DecimalField(
        **MONEY,
        default=ZERO,
        validators=[MinValueValidator(ZERO)],
        help_text="A flat amount on top of the percentage, in the movement's currency.",
    )
    basis = models.CharField(
        max_length=8,
        choices=Basis.choices,
        default=str(Basis.GROSS),
        help_text=(
            "What the percentage is of. `The charges before it` is how VAT on a commission works."
        ),
    )
    minimum = models.DecimalField(
        **MONEY,
        default=ZERO,
        validators=[MinValueValidator(ZERO)],
        help_text="A floor on this charge. Zero means none.",
    )
    maximum = models.DecimalField(
        **MONEY,
        default=ZERO,
        validators=[MinValueValidator(ZERO)],
        help_text="A cap on this charge. Zero means none.",
    )
    absorbed = models.BooleanField(
        default=False,
        help_text=(
            "Recorded but not charged: a cost this deployment pays itself. The "
            "customer's amount is unaffected."
        ),
    )
    is_enabled = models.BooleanField(default=True)
    position = models.IntegerField(
        default=0,
        help_text="Order of application. It matters: a tax on the charges before it reads this.",
    )

    class Meta:
        ordering = ["position", "kind"]
        indexes = [models.Index(fields=["method", "is_enabled", "position"])]

    def __str__(self) -> str:
        return f"{self.display_label} on {self.method.code}"

    @property
    def display_label(self) -> str:
        return self.label or self.get_kind_display()

    def covers(self, direction: str, currency: str) -> bool:
        """Whether this fee applies to a movement going that way in that currency."""
        if not self.is_enabled or not Applies.covers(self.applies_to, direction):
            return False
        return self.currency_id is None or self.currency.currency == currency.upper()

    def clean(self) -> None:
        """Refuse the two fees that would be silently wrong.

        A fee on a rail with no counterparty -- an internal transfer, a system
        adjustment -- is money charged for moving money that never left, and a fee
        that costs nothing at all is a row somebody meant to fill in.
        """
        if self.method_id and not self.method.chargeable:
            raise ValidationError(
                f"{self.method.spec.label} moves money that never leaves this app, so "
                "there is nothing to charge for. Transfers between wallets here are free."
            )
        if not self.percent and not self.fixed:
            raise ValidationError("A fee with no percentage and no fixed amount costs nothing.")
        if self.currency_id and self.method_id and self.currency.method_id != self.method_id:
            raise ValidationError({"currency": "That currency belongs to a different method."})
        if self.maximum and self.minimum and self.maximum < self.minimum:
            raise ValidationError({"maximum": "The cap is below the floor."})


class ExchangeRateQuerySet(models.QuerySet["ExchangeRate"]):
    def live(self, *, now: Any = None) -> "ExchangeRateQuerySet":
        """Rates that are switched on and have come into effect."""
        return self.filter(is_enabled=True, effective_from__lte=now or timezone.now())


class ExchangeRate(models.Model):
    """What one unit of ``base`` is worth in ``quote``, and what this deployment keeps.

    Rows are never edited, only superseded: a new row with a later
    ``effective_from`` becomes the live rate, and the old one stays as the answer
    to "what did we convert at, on the day we converted?". A conversion that
    cannot be explained six months later is a conversion that gets disputed.

    ``margin_percent`` is the spread, and it is always applied against the
    customer -- that is what a spread is. Converting 100 at a rate of 1.1 with a
    1% margin gives the customer 108.90, not 111.10, whichever way round the pair
    is quoted.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    base = models.CharField(max_length=12, help_text="The currency being converted from.")
    quote = models.CharField(max_length=12, help_text="The currency being converted to.")
    rate = models.DecimalField(
        **RATE,
        validators=[MinValueValidator(Decimal("0"))],
        help_text="One unit of base, in quote. Twelve decimals, because some pairs need them.",
    )
    margin_percent = models.DecimalField(
        **PERCENT,
        default=Decimal("0"),
        validators=[MinValueValidator(Decimal("0")), MaxValueValidator(Decimal("100"))],
        help_text="The spread kept on a conversion, always taken against the customer.",
    )
    source = models.CharField(
        max_length=120,
        blank=True,
        help_text="Where the number came from: a provider's name, or who typed it in.",
    )
    is_enabled = models.BooleanField(default=True)
    effective_from = models.DateTimeField(
        default=timezone.now, help_text="When this rate takes over from the one before it."
    )
    created_at = models.DateTimeField(default=timezone.now, editable=False)

    objects = ExchangeRateQuerySet.as_manager()

    class Meta:
        ordering = ["-effective_from", "-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["base", "quote", "effective_from"], name="wallet_rate_once_per_moment"
            ),
            models.CheckConstraint(
                condition=models.Q(rate__gt=Decimal("0")),
                name="wallet_rate_positive",
                violation_error_message="A rate of zero converts every amount to nothing.",
            ),
        ]
        indexes = [models.Index(fields=["base", "quote", "-effective_from"])]

    def __str__(self) -> str:
        return f"{self.base}/{self.quote} @ {self.rate}"

    def save(self, *args: Any, **kwargs: Any) -> None:
        self.base, self.quote = self.base.upper(), self.quote.upper()
        super().save(*args, **kwargs)

    def clean(self) -> None:
        if self.base and self.quote and self.base.upper() == self.quote.upper():
            raise ValidationError({"quote": "A currency is always worth one of itself."})
