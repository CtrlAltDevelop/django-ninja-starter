"""Moving money: what settles, what waits on a person, and what a balance says.

The tests here are about the rules that would cost somebody money if they were
wrong -- a pending entry counted as balance, a payout allowed twice, a request
that settled without anybody applying it -- rather than about the plumbing.
"""

from decimal import Decimal
from typing import Any

import pytest
from django.test import override_settings

from apps.wallet.catalog import PaymentMethod
from apps.wallet.errors import (
    ApprovalRequired,
    InsufficientFunds,
    InvalidDestination,
    InvalidTransition,
    MethodNotAllowed,
    WalletFrozen,
)
from apps.wallet.models import Approval, EntryStatus, WalletCharge, WalletStatus
from apps.wallet.services import page_size, wallet_service

pytestmark = pytest.mark.django_db


# -- what a balance is made of -----------------------------------------------


def test_only_settled_entries_are_money(alice: Any, card: PaymentMethod) -> None:
    """A card deposit is recorded, visible, and worth nothing until it confirms."""
    wallet_service.deposit(alice, amount=Decimal("100"), method="card", reference="a")

    balance = wallet_service.balance(alice)
    assert balance["settled"] == Decimal("0.0000")
    assert balance["incoming"] == Decimal("96.1600")
    assert balance["available"] == Decimal("0.0000")
    # The optimistic number is published, and named so nobody authorises on it.
    assert balance["projected"] == Decimal("96.1600")


def test_a_pending_payout_holds_its_own_money(funded: Any, cash: PaymentMethod) -> None:
    """Available is settled less what is already spoken for.

    Otherwise the same thousand could be promised twice, and somebody would have
    to decide afterwards which of the two payouts to fail.
    """
    slow = PaymentMethod.objects.create(
        code="wire",
        name="Wire",
        rail="wire",
        is_enabled=True,
        supports_withdrawal=True,
        supports_deposit=False,
        requires_approval=False,
    )
    slow.currencies.create(currency="USD")
    wallet_service.withdraw(
        funded, amount=Decimal("400"), method="wire", reference="w1", destination="DE89"
    )

    balance = wallet_service.balance(funded)
    assert balance["settled"] == Decimal("1000.0000")
    assert balance["outgoing"] == Decimal("400.0000")
    assert balance["available"] == Decimal("600.0000")


def test_a_withdrawal_beyond_available_is_refused(funded: Any, cash: PaymentMethod) -> None:
    with pytest.raises(InsufficientFunds, match="1000"):
        wallet_service.withdraw(funded, amount=Decimal("1500"), method="cash", reference="too-much")


def test_a_frozen_wallet_still_takes_money_in(funded: Any, cash: PaymentMethod) -> None:
    """Which is what a compliance hold means. Refusing both would strand money in flight."""
    wallet = wallet_service.wallet_for(funded)
    wallet.status = str(WalletStatus.FROZEN)
    wallet.save()

    wallet_service.deposit(funded, amount=Decimal("10"), method="cash", reference="in")
    with pytest.raises(WalletFrozen):
        wallet_service.withdraw(funded, amount=Decimal("10"), method="cash", reference="out")


def test_a_retry_returns_the_first_entry_rather_than_moving_money_twice(
    alice: Any, cash: PaymentMethod
) -> None:
    """The one thing a payment client will certainly do is retry on a timeout."""
    first = wallet_service.deposit(alice, amount=Decimal("50"), method="cash", reference="k")
    again = wallet_service.deposit(alice, amount=Decimal("50"), method="cash", reference="k")

    assert first["id"] == again["id"]
    assert wallet_service.balance(alice)["settled"] == Decimal("50.0000")


# -- the request flow --------------------------------------------------------


def test_a_method_requiring_approval_produces_a_request(alice: Any, counter: PaymentMethod) -> None:
    """Cash settles at once -- and still waits, because the method asks for a person.

    The two questions are independent, and this is the test that says so: the rail
    would have settled this immediately, and it is pending anyway.
    """
    entry = wallet_service.deposit(alice, amount=Decimal("100"), method="counter", reference="r")

    assert entry["status"] == str(EntryStatus.PENDING)
    assert entry["approval"] == str(Approval.REQUESTED)
    assert entry["awaiting_approval"] is True
    assert wallet_service.balance(alice)["settled"] == Decimal("0.0000")


def test_a_request_cannot_settle_until_it_is_applied(alice: Any, counter: PaymentMethod) -> None:
    """And the refusal says it is waiting on a person, not that the state machine forbade it."""
    entry = wallet_service.deposit(alice, amount=Decimal("100"), method="counter", reference="r")

    with pytest.raises(ApprovalRequired, match="operator"):
        wallet_service.settle(alice, entry["id"])


def test_applying_a_request_on_an_immediate_rail_settles_it(
    alice: Any, counter: PaymentMethod, operator: object
) -> None:
    """The approval *was* the confirmation: somebody counted the cash."""
    entry = wallet_service.deposit(alice, amount=Decimal("100"), method="counter", reference="r")
    applied = wallet_service.approve(entry["id"], by=operator, note="counted it")

    assert applied["approval"] == str(Approval.APPROVED)
    assert applied["status"] == str(EntryStatus.DONE)
    assert applied["review_note"] == "counted it"
    assert wallet_service.balance(alice)["settled"] == Decimal("100.0000")


def test_applying_a_request_on_a_slow_rail_leaves_it_pending(
    alice: Any, card: PaymentMethod, operator: object
) -> None:
    """An operator agreeing a card deposit should happen is not the processor confirming it."""
    card.requires_approval = True
    card.save()
    entry = wallet_service.deposit(alice, amount=Decimal("100"), method="card", reference="r")

    applied = wallet_service.approve(entry["id"], by=operator)
    assert applied["approval"] == str(Approval.APPROVED)
    assert applied["status"] == str(EntryStatus.PENDING)

    # And now the rail's own confirmation can land.
    settled = wallet_service.settle(alice, entry["id"], external_reference="ch_1")
    assert settled["status"] == str(EntryStatus.DONE)


def test_refusing_a_request_cancels_it_and_releases_the_money(
    funded: Any, counter: PaymentMethod, operator: object
) -> None:
    """One operation, because a refused request that stayed pending would still hold funds."""
    entry = wallet_service.withdraw(funded, amount=Decimal("200"), method="counter", reference="r")
    assert wallet_service.balance(funded)["available"] == Decimal("800.0000")

    refused = wallet_service.reject(entry["id"], by=operator, note="no documents")
    assert refused["approval"] == str(Approval.REJECTED)
    assert refused["status"] == str(EntryStatus.CANCELLED)
    assert wallet_service.balance(funded)["available"] == Decimal("1000.0000")


def test_approving_twice_is_not_an_error(
    alice: Any, counter: PaymentMethod, operator: object
) -> None:
    """Double-clicked buttons and re-delivered webhooks both happen."""
    entry = wallet_service.deposit(alice, amount=Decimal("10"), method="counter", reference="r")
    once = wallet_service.approve(entry["id"], by=operator)
    twice = wallet_service.approve(entry["id"], by=operator)
    assert once["id"] == twice["id"]


def test_a_refused_request_cannot_then_be_applied(
    alice: Any, counter: PaymentMethod, operator: object
) -> None:
    entry = wallet_service.deposit(alice, amount=Decimal("10"), method="counter", reference="r")
    wallet_service.reject(entry["id"], by=operator)
    with pytest.raises(InvalidTransition, match="rejected"):
        wallet_service.approve(entry["id"], by=operator)


def test_the_queue_is_oldest_first(alice: Any, bob: Any, counter: PaymentMethod) -> None:
    """Because the cost of this queue is somebody's deposit sitting unapplied."""
    first = wallet_service.deposit(alice, amount=Decimal("10"), method="counter", reference="a")
    second = wallet_service.deposit(bob, amount=Decimal("20"), method="counter", reference="b")

    queue = wallet_service.awaiting_approval()
    assert [row["id"] for row in queue] == [first["id"], second["id"]]


# -- what a record shows -----------------------------------------------------


def test_the_charges_are_written_onto_the_entry(alice: Any, card: PaymentMethod) -> None:
    """Copied, not referenced -- see WalletCharge. The receipt outlives the price list."""
    entry = wallet_service.deposit(alice, amount=Decimal("100"), method="card", reference="r")

    assert entry["gross_amount"] == Decimal("100.0000")
    assert entry["fee_total"] == Decimal("3.8400")
    assert entry["net_amount"] == Decimal("96.1600")
    assert entry["amount"] == Decimal("96.1600")
    assert [charge["label"] for charge in entry["charges"]] == ["Processing", "VAT"]


def test_editing_a_fee_does_not_rewrite_an_old_receipt(alice: Any, card: PaymentMethod) -> None:
    """What somebody was charged in March is not configuration."""
    entry = wallet_service.deposit(alice, amount=Decimal("100"), method="card", reference="r")
    card.fees.filter(label="Processing").update(percent=Decimal("50"))

    unchanged = wallet_service.entry(alice, entry["id"])
    assert unchanged["fee_total"] == Decimal("3.8400")
    assert WalletCharge.objects.get(entry_id=entry["id"], label="Processing").percent == Decimal(
        "2.9000"
    )


def test_a_converted_deposit_records_the_rate_it_used(
    alice: Any, card: PaymentMethod, eur_rate: object
) -> None:
    """Answerable years later, when the rate row that priced it has been superseded."""
    entry = wallet_service.deposit(
        alice, amount=Decimal("100"), method="card", reference="r", currency="EUR"
    )
    assert entry["currency"] == "EUR"
    assert entry["wallet_currency"] == "USD"
    assert entry["converted"] is True
    assert entry["exchange_rate"] == Decimal("1.10")
    assert entry["amount"] == Decimal("105.7760")


# -- payouts -----------------------------------------------------------------


def test_a_payout_needs_somewhere_to_go(funded: Any, card: PaymentMethod) -> None:
    with pytest.raises(InvalidDestination, match="somewhere to go"):
        wallet_service.withdraw(funded, amount=Decimal("50"), method="card", reference="r")


def test_a_payout_to_an_address_on_the_wrong_chain_is_refused(
    funded: Any, crypto: PaymentMethod, usdt_rate: object
) -> None:
    """The chain would not refuse it. It would deliver the money to nobody."""
    with pytest.raises(InvalidDestination, match="wrong chain"):
        wallet_service.withdraw(
            funded,
            amount=Decimal("100"),
            method="usdt",
            reference="r",
            currency="USDT",
            network="trc20",
            destination="0x" + "a" * 40,
        )


# -- transfers ---------------------------------------------------------------


def test_a_transfer_between_wallets_here_is_free(
    funded: Any, bob: Any, cash: PaymentMethod
) -> None:
    """The amount that leaves one wallet is the amount that arrives in the other.

    Bob has no wallet until this runs: a transfer opens the recipient's, which is
    what "every account has a wallet" has to mean in practice.
    """
    sent = wallet_service.transfer(funded, to_user=bob, amount=Decimal("250"), reference="t1")

    assert sent["fee_total"] == Decimal("0.0000")
    assert sent["gross_amount"] == Decimal("250.0000")
    assert sent["amount"] == Decimal("250.0000")
    assert sent["charges"] == []
    assert wallet_service.balance(bob)["settled"] == Decimal("250.0000")
    assert wallet_service.balance(funded)["settled"] == Decimal("750.0000")


def test_a_transfer_settles_at_once_and_needs_nobody(
    funded: Any, bob: Any, cash: PaymentMethod
) -> None:
    """There is no rail in the middle, so there is nothing to confirm or approve."""
    sent = wallet_service.transfer(funded, to_user=bob, amount=Decimal("10"), reference="t")
    assert sent["status"] == str(EntryStatus.DONE)
    assert sent["approval"] == str(Approval.NOT_REQUIRED)
    assert sent["counterparty_id"] is not None


def test_a_transfer_cannot_outrun_the_balance(funded: Any, bob: Any, cash: PaymentMethod) -> None:
    with pytest.raises(InsufficientFunds):
        wallet_service.transfer(funded, to_user=bob, amount=Decimal("5000"), reference="t")


# -- the catalogue ------------------------------------------------------------


def test_a_disabled_method_cannot_be_used_even_by_name(alice: Any, cash: PaymentMethod) -> None:
    cash.is_enabled = False
    cash.save()
    with pytest.raises(MethodNotAllowed, match="not available"):
        wallet_service.deposit(alice, amount=Decimal("10"), method="cash", reference="r")


def test_a_method_with_no_currency_is_not_published(db: None) -> None:
    """It would be a choice that refuses everybody who picks it."""
    PaymentMethod.objects.create(code="half-done", name="Half done", rail="cash", is_enabled=True)
    assert [row["code"] for row in wallet_service.methods()] == []


def test_a_rail_the_deployment_does_not_run_is_not_published(
    cash: PaymentMethod, settings: object
) -> None:
    """The outer gate: an administrator cannot reach a rail by adding a row."""
    settings.WALLET_METHODS = ("card",)  # type: ignore[attr-defined]
    assert [row["code"] for row in wallet_service.methods()] == []


def test_an_absorbed_fee_is_not_in_the_published_price_list(cash: PaymentMethod) -> None:
    """It costs the customer nothing, so it is not a price."""
    cash.fees.create(kind="cost", percent=Decimal("1"), absorbed=True)
    cash.fees.create(kind="commission", percent=Decimal("2"))

    published = wallet_service.method("cash")
    assert [fee["kind"] for fee in published["fees"]] == ["commission"]


def test_a_balance_is_published_at_the_precision_money_is_held_at(
    funded: Any, card: PaymentMethod, usdt_rate: Any, crypto: PaymentMethod
) -> None:
    """Four decimal places, whatever the database hands back from a SUM.

    A converted movement leaves an amount that a database is free to widen when
    it adds it up -- SQLite returns a dozen decimal places -- and a balance
    published as `115.9600000000000` is a number no interface should have to
    render and no client should have to round.
    """
    wallet_service.withdraw(
        funded,
        amount=Decimal("20"),
        method="usdt",
        reference="w",
        currency="USDT",
        network="trc20",
        destination="T" + "9" * 33,
    )

    balance = wallet_service.balance(funded)

    for field in ("settled", "available", "incoming", "outgoing", "projected"):
        assert balance[field].as_tuple().exponent == -4, f"{field} is {balance[field]}"


def test_a_reversal_moves_the_balance_back_and_says_what_on(
    alice: Any, card: PaymentMethod
) -> None:
    """The original stays as it was; the money comes back on a second entry.

    The correction carries the method it went back on and a gross that matches
    what it moved -- a record showing 96.16 moved and a gross of nothing is a
    receipt nobody can read.
    """
    deposit = wallet_service.deposit(alice, amount=Decimal("100"), method="card", reference="d")
    wallet_service.settle(alice, deposit["id"])

    correction = wallet_service.reverse(
        alice, deposit["id"], reference="chargeback-1", reason="disputed"
    )

    assert correction["kind"] == "chargeback"
    assert correction["amount"] == Decimal("96.1600")
    assert correction["gross_amount"] == Decimal("96.1600")
    # The fee is not returned: it was spent moving money that really did move.
    assert correction["fee_total"] == Decimal("0.0000")
    assert correction["payment_method"] == "card"
    assert wallet_service.balance(alice)["settled"] == Decimal("0.0000")

    original = wallet_service.entry(alice, deposit["id"])
    assert original["status"] == "reversed"
    assert original["gross_amount"] == Decimal("100.0000")


def test_a_reversal_does_not_erase_when_the_original_settled(
    alice: Any, cash: PaymentMethod
) -> None:
    """`when did this clear?` starts being asked the moment a chargeback lands.

    A reversed entry still counts towards the balance, so a record that also
    claims never to have settled is telling two incompatible stories about the
    same money.
    """
    deposit = wallet_service.deposit(alice, amount=Decimal("100"), method="cash", reference="d")
    settled_at = wallet_service.entry(alice, deposit["id"])["settled_at"]
    assert settled_at is not None

    wallet_service.reverse(alice, deposit["id"], reference="cb")

    original = wallet_service.entry(alice, deposit["id"])
    assert original["status"] == "reversed"
    assert original["settled_at"] == settled_at
    assert original["counts_towards_balance"] is True


def test_a_record_says_whether_it_is_part_of_the_balance(
    alice: Any, cash: PaymentMethod, card: PaymentMethod
) -> None:
    """Add up every movement that counts and you get the balance. That is the contract."""
    wallet_service.deposit(alice, amount=Decimal("100"), method="cash", reference="a")
    reversed_one = wallet_service.deposit(alice, amount=Decimal("40"), method="cash", reference="b")
    wallet_service.reverse(alice, reversed_one["id"], reference="cb")
    failed = wallet_service.deposit(alice, amount=Decimal("500"), method="card", reference="c")
    wallet_service.fail(alice, failed["id"])

    rows = wallet_service.entries(alice)
    counted = sum(
        (row["signed_amount"] for row in rows if row["counts_towards_balance"]), Decimal("0")
    )

    assert counted == wallet_service.balance(alice)["settled"] == Decimal("100.0000")
    # The failed one is visible and worth nothing; the reversed one counts and
    # is no longer `settled`.
    by_reference = {row["reference"]: row for row in rows}
    assert by_reference["c"]["counts_towards_balance"] is False
    assert by_reference["b"]["counts_towards_balance"] is True
    assert by_reference["b"]["settled"] is False


def test_applying_a_payout_the_balance_no_longer_covers_changes_nothing(
    alice: Any, cash: PaymentMethod, counter: PaymentMethod, operator: Any
) -> None:
    """All or nothing, deliberately.

    The alternative leaves a payout marked approved but unsettled -- a standing
    authorisation that would fire the moment the balance recovered, with nobody
    looking at it a second time.
    """
    wallet_service.deposit(alice, amount=Decimal("100"), method="cash", reference="seed")
    payout = wallet_service.withdraw(alice, amount=Decimal("90"), method="counter", reference="w")
    # The money goes while the request sits in the queue.
    wallet_service.wallet_for(alice).entries.filter(reference="seed").update(amount=Decimal("10"))

    with pytest.raises(InsufficientFunds):
        wallet_service.approve(payout["id"], by=operator)

    still = wallet_service.entry(alice, payout["id"])
    assert still["approval"] == Approval.REQUESTED
    assert still["status"] == EntryStatus.PENDING


def test_a_deployment_decides_how_big_a_page_is(alice: Any, cash: PaymentMethod) -> None:
    """The settings are declared and validated, so they have to be read.

    A setting a deployment can set, a check can validate, and nothing actually
    honours is worse than no setting at all: it reads as a promise.
    """
    for index in range(12):
        wallet_service.deposit(alice, amount=Decimal("1"), method="cash", reference=f"p{index}")

    with override_settings(WALLET_PAGE_SIZE=5, WALLET_MAX_PAGE_SIZE=8):
        assert len(wallet_service.entries(alice)) == 5
        assert len(wallet_service.entries(alice, limit=500)) == 8
        # And a transport echoes back what it served, not what was asked for.
        assert page_size(500) == 8
        assert page_size(None) == 5


def test_a_movement_that_is_already_over_cannot_be_applied(
    alice: Any, counter: PaymentMethod, operator: Any
) -> None:
    """Approval and status are independent, which is what makes this reachable.

    The account cancels a request; its approval stays `requested` for the
    record. Nothing in the state machine stops an operator applying it, and
    while no money would move, the trail would end up saying somebody approved a
    payment that had already been withdrawn.
    """
    request = wallet_service.deposit(alice, amount=Decimal("50"), method="counter", reference="r")
    wallet_service.cancel(alice, request["id"])

    with pytest.raises(InvalidTransition, match="already cancelled"):
        wallet_service.approve(request["id"], by=operator)
    with pytest.raises(InvalidTransition, match="already cancelled"):
        wallet_service.reject(request["id"], by=operator)

    unchanged = wallet_service.entry(alice, request["id"])
    assert unchanged["approval"] == Approval.REQUESTED
    assert unchanged["status"] == EntryStatus.CANCELLED


def test_the_queue_holds_only_what_somebody_can_still_act_on(
    alice: Any, bob: Any, counter: PaymentMethod
) -> None:
    """A request nobody can apply or refuse would sit there unclearable for ever."""
    live = wallet_service.deposit(alice, amount=Decimal("10"), method="counter", reference="a")
    dead = wallet_service.deposit(bob, amount=Decimal("20"), method="counter", reference="b")
    wallet_service.cancel(bob, dead["id"])

    queue = [row["id"] for row in wallet_service.awaiting_approval()]

    assert queue == [live["id"]]
