"""What a movement costs: the fee arithmetic, the limits, and the exchange rate.

These are the tests that would catch somebody being overcharged, so they check
the numbers rather than the shapes. Every expected figure below is one that can
be worked out by hand from the fixture, on purpose -- a test asserting that the
fee equals whatever the code computed would pass just as happily with the
decimal point in the wrong place.
"""

from decimal import Decimal
from typing import Any

import pytest
from django.core.exceptions import ValidationError

from apps.wallet.catalog import Applies, ChargeKind, MethodFee, MethodNetwork, PaymentMethod
from apps.wallet.charges import convert, quote_movement
from apps.wallet.errors import (
    CurrencyNotAllowed,
    InvalidAmount,
    NetworkRequired,
    NoExchangeRate,
)
from apps.wallet.methods import Direction, Method

pytestmark = pytest.mark.django_db


def test_a_commission_and_the_tax_on_it_are_two_lines(card: PaymentMethod) -> None:
    """2.9% + 0.30 on 100 is 3.20, and 20% VAT on that fee is 0.64.

    The point of the second assertion is the basis: the VAT is a fifth of the
    *commission*, not a fifth of the hundred. A deployment that got this wrong
    would charge 20.00 instead of 0.64 and would not find out from its code.
    """
    quote = quote_movement(
        card,
        direction=str(Direction.CREDIT),
        amount=Decimal("100"),
        currency="USD",
        wallet_currency="USD",
    )
    commission, tax = quote.charges
    assert commission.kind == str(ChargeKind.COMMISSION)
    assert commission.amount == Decimal("3.2000")
    assert tax.kind == str(ChargeKind.TAX)
    assert tax.amount == Decimal("0.6400")
    assert quote.fee_total == Decimal("3.8400")


def test_a_deposit_credits_what_survived_the_charges(card: PaymentMethod) -> None:
    """Pay 100 in, 3.84 comes off, 96.16 reaches the wallet."""
    quote = quote_movement(
        card,
        direction=str(Direction.CREDIT),
        amount=Decimal("100"),
        currency="USD",
        wallet_currency="USD",
    )
    assert quote.gross == Decimal("100.0000")
    assert quote.wallet_amount == Decimal("96.1600")
    # What moves on the rail is the whole hundred: that is what the customer pays.
    assert quote.settlement_amount == Decimal("100.0000")


def test_a_withdrawal_debits_the_whole_amount_and_pays_out_the_rest(card: PaymentMethod) -> None:
    """The same rule read from the other end, which is what makes limits mean one thing.

    100 leaves the wallet and 96.16 reaches the customer -- rather than 103.84
    leaving to deliver 100, which would make a maximum of 100 unreachable by
    anybody asking for exactly 100.
    """
    quote = quote_movement(
        card,
        direction=str(Direction.DEBIT),
        amount=Decimal("100"),
        currency="USD",
        wallet_currency="USD",
    )
    assert quote.wallet_amount == Decimal("100.0000")
    assert quote.settlement_amount == Decimal("96.1600")
    assert quote.net == Decimal("96.1600")


def test_an_absorbed_fee_is_recorded_and_costs_the_customer_nothing(
    cash: PaymentMethod,
) -> None:
    """A cost this deployment pays out of its own margin still has to be written down."""
    MethodFee.objects.create(
        method=cash,
        kind=str(ChargeKind.COST),
        label="Interchange",
        percent=Decimal("1"),
        absorbed=True,
    )
    quote = quote_movement(
        cash,
        direction=str(Direction.CREDIT),
        amount=Decimal("100"),
        currency="USD",
        wallet_currency="USD",
    )
    assert quote.charges[0].amount == Decimal("1.0000")
    assert quote.absorbed_total == Decimal("1.0000")
    # And none of it reached the customer's side of the arithmetic.
    assert quote.fee_total == Decimal("0.0000")
    assert quote.wallet_amount == Decimal("100.0000")


def test_a_fee_is_capped_and_floored_where_the_rule_says(cash: PaymentMethod) -> None:
    MethodFee.objects.create(
        method=cash,
        kind=str(ChargeKind.COMMISSION),
        percent=Decimal("10"),
        minimum=Decimal("2"),
        maximum=Decimal("5"),
    )

    def fee(amount: str) -> Decimal:
        return quote_movement(
            cash,
            direction=str(Direction.CREDIT),
            amount=Decimal(amount),
            currency="USD",
            wallet_currency="USD",
        ).fee_total

    assert fee("10") == Decimal("2.0000")  # 1.00 raised to the floor
    assert fee("30") == Decimal("3.0000")  # between the two, untouched
    assert fee("100") == Decimal("5.0000")  # 10.00 held down to the cap


def test_a_fee_can_apply_to_one_direction_only(cash: PaymentMethod) -> None:
    """A payout fee is not a deposit fee, and most real ones are asymmetric."""
    MethodFee.objects.create(
        method=cash,
        kind=str(ChargeKind.SERVICE),
        applies_to=str(Applies.DEBIT),
        fixed=Decimal("2"),
    )

    def fee(direction: str) -> Decimal:
        return quote_movement(
            cash,
            direction=direction,
            amount=Decimal("50"),
            currency="USD",
            wallet_currency="USD",
        ).fee_total

    assert fee(str(Direction.CREDIT)) == Decimal("0.0000")
    assert fee(str(Direction.DEBIT)) == Decimal("2.0000")


def test_a_movement_swallowed_entirely_by_its_fees_is_refused(cash: PaymentMethod) -> None:
    """Better a refusal than a deposit that credits nothing and takes the lot."""
    MethodFee.objects.create(method=cash, kind=str(ChargeKind.COMMISSION), fixed=Decimal("50"))
    with pytest.raises(InvalidAmount, match="the whole of it"):
        quote_movement(
            cash,
            direction=str(Direction.CREDIT),
            amount=Decimal("50"),
            currency="USD",
            wallet_currency="USD",
        )


def test_limits_are_checked_against_what_the_customer_asked_for(card: PaymentMethod) -> None:
    """The floor is 5, so 4 is refused -- and the message says which currency it is in."""
    with pytest.raises(InvalidAmount, match="5"):
        quote_movement(
            card,
            direction=str(Direction.CREDIT),
            amount=Decimal("4"),
            currency="USD",
            wallet_currency="USD",
        )
    with pytest.raises(InvalidAmount, match="10000"):
        quote_movement(
            card,
            direction=str(Direction.CREDIT),
            amount=Decimal("20000"),
            currency="USD",
            wallet_currency="USD",
        )


def test_a_currency_the_method_does_not_take_is_refused_by_name(card: PaymentMethod) -> None:
    """And the refusal says what it does take, because that is the next question."""
    with pytest.raises(CurrencyNotAllowed, match="EUR, USD"):
        quote_movement(
            card,
            direction=str(Direction.CREDIT),
            amount=Decimal("50"),
            currency="JPY",
            wallet_currency="USD",
        )


# -- chains ------------------------------------------------------------------


def test_a_crypto_movement_without_a_chain_is_refused_rather_than_guessed(
    crypto: PaymentMethod, usdt_rate: object
) -> None:
    """Never defaulted, even to the only chain configured today.

    This is the one that matters: a default that is right once is wrong the
    moment a second chain is added, and the failure is a payout to an address
    nobody holds a key for.
    """
    with pytest.raises(NetworkRequired, match="trc20"):
        quote_movement(
            crypto,
            direction=str(Direction.DEBIT),
            amount=Decimal("100"),
            currency="USDT",
            wallet_currency="USD",
        )


def test_each_chain_charges_its_own_fee(crypto: PaymentMethod, usdt_rate: Any) -> None:
    """The same asset costs 1 on Tron and 8 on Ethereum, and that is the network's fact."""

    def fee(chain: str) -> Decimal:
        return quote_movement(
            crypto,
            direction=str(Direction.DEBIT),
            amount=Decimal("100"),
            currency="USDT",
            wallet_currency="USD",
            network_code=chain,
        ).fee_total

    assert fee("trc20") == Decimal("1.0000")
    assert fee("erc20") == Decimal("8.0000")


def test_a_network_fee_is_charged_on_the_way_out_and_not_on_the_way_in(
    crypto: PaymentMethod, usdt_rate: object
) -> None:
    """Somebody depositing has already paid the chain themselves."""
    incoming = quote_movement(
        crypto,
        direction=str(Direction.CREDIT),
        amount=Decimal("100"),
        currency="USDT",
        wallet_currency="USD",
        network_code="trc20",
    )
    assert incoming.fee_total == Decimal("0.0000")


def test_an_address_is_checked_against_its_own_chain(crypto: PaymentMethod) -> None:
    tron = crypto.currencies.get().networks.get(code="trc20")
    ethereum = crypto.currencies.get().networks.get(code="erc20")
    ether_address = "0x" + "a" * 40

    assert ethereum.accepts_address(ether_address)
    # Well-formed, and for the wrong chain. The network would accept this and the
    # money would be gone, which is why it is refused before it is sent.
    assert not tron.accepts_address(ether_address)


# -- conversion --------------------------------------------------------------


def test_a_conversion_takes_the_spread_against_the_customer(eur_rate: Any) -> None:
    """Less comes in than the rate says, more goes out than it says. Both ways."""
    eur_rate.margin_percent = Decimal("2")
    eur_rate.save()

    incoming = convert(Decimal("100"), "EUR", "USD", direction=str(Direction.CREDIT))
    outgoing = convert(Decimal("100"), "EUR", "USD", direction=str(Direction.DEBIT))

    assert incoming.converted == Decimal("107.8000")  # 110 less 2%
    assert outgoing.converted == Decimal("112.2000")  # 110 plus 2%
    assert incoming.rate == Decimal("1.10")


def test_a_pair_quoted_one_way_converts_both_ways(eur_rate: Any) -> None:
    """The reciprocal, rather than a second row somebody has to keep in step."""
    back = convert(Decimal("110"), "USD", "EUR", direction=str(Direction.CREDIT))
    assert back.inverted is True
    assert back.converted == Decimal("100.0000")


def test_a_currency_is_always_worth_one_of_itself() -> None:
    """No rate row needed, and none consulted."""
    same = convert(Decimal("50"), "USD", "USD", direction=str(Direction.CREDIT))
    assert same.converted == Decimal("50.0000")
    assert same.is_identity


def test_a_pair_with_no_rate_is_refused_rather_than_converted_at_one() -> None:
    """The alternative is selling currency at a rate nobody chose."""
    with pytest.raises(NoExchangeRate, match="GBP/USD"):
        convert(Decimal("10"), "GBP", "USD", direction=str(Direction.CREDIT))


def test_a_deposit_in_another_currency_converts_after_its_charges(
    card: PaymentMethod, eur_rate: object
) -> None:
    """Charged in euros, converted into the wallet's dollars, in that order.

    Order matters and is worth pinning: a percentage fee applied after conversion
    is the same number, but a *fixed* fee is not -- 0.30 is 0.30 euros here, not
    0.30 dollars.
    """
    quote = quote_movement(
        card,
        direction=str(Direction.CREDIT),
        amount=Decimal("100"),
        currency="EUR",
        wallet_currency="USD",
    )
    assert quote.currency == "EUR"
    assert quote.fee_total == Decimal("3.8400")  # in euros
    assert quote.net == Decimal("96.1600")  # in euros
    assert quote.wallet_amount == Decimal("105.7760")  # 96.16 * 1.10, in dollars
    assert quote.converted is True


# -- the rules a configuration cannot break ----------------------------------


def test_a_fee_cannot_be_attached_to_an_internal_transfer() -> None:
    """Refused at the point somebody tries to save it, not silently ignored later.

    Transfers between two wallets here are free, and this is what makes that a
    property of the app rather than a default that an afternoon in the admin
    could quietly change.
    """
    internal = PaymentMethod.objects.create(
        code="internal", name="Wallet to wallet", rail=str(Method.INTERNAL)
    )
    fee = MethodFee(method=internal, kind=str(ChargeKind.COMMISSION), percent=Decimal("1"))
    with pytest.raises(ValidationError, match="never leaves this app"):
        fee.full_clean()


def test_a_fee_that_costs_nothing_is_refused(cash: PaymentMethod) -> None:
    """A row with no percentage and no fixed amount is one somebody meant to fill in."""
    with pytest.raises(ValidationError, match="costs nothing"):
        MethodFee(method=cash, kind=str(ChargeKind.COMMISSION)).full_clean()


def test_a_method_cannot_claim_a_direction_its_rail_does_not_carry() -> None:
    """A voucher takes money in. Paying somebody out in vouchers is a different product."""
    method = PaymentMethod(
        code="voucher",
        name="Voucher",
        rail=str(Method.VOUCHER),
        supports_deposit=True,
        supports_withdrawal=True,
    )
    with pytest.raises(ValidationError, match="does not pay money out"):
        method.full_clean()


def test_a_chain_saved_in_capitals_is_still_reachable(crypto: PaymentMethod) -> None:
    """Chain codes are looked up in lower case, so they are stored that way.

    A row saved as `TRC20` would otherwise be a chain nothing can reach: every
    payout naming it refused as "not carried here", while the row sits in the
    admin looking perfectly configured.
    """
    asset = crypto.currencies.get()
    saved = MethodNetwork.objects.create(asset=asset, code="BEP20", name="BNB Smart Chain")

    assert saved.code == "bep20"
    quote = quote_movement(
        crypto,
        direction=str(Direction.DEBIT),
        amount=Decimal("100"),
        currency="USDT",
        wallet_currency="USDT",
        network_code="BEP20",
    )
    assert quote.network_code == "bep20"
