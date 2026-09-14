"""Corrections by hand, spending inside the project, and a wallet's status.

Three things the service does that no client can ask for. What is worth pinning
about each is not that it writes a row -- everything here writes a row -- but
what it refuses: a kind the back office does not write, a debit past the
overdraft allowance, and a close that would strand money or a pending movement.
"""

from decimal import Decimal
from typing import Any

import pytest

from apps.wallet.catalog import PaymentMethod
from apps.wallet.errors import InvalidTransition, WalletError
from apps.wallet.models import EntryKind, Wallet, WalletStatus
from apps.wallet.services import wallet_service

pytestmark = pytest.mark.django_db


def _wallet(user: Any) -> Wallet:
    return Wallet.objects.get(user=user)


def test_a_bonus_settles_at_once_and_costs_nothing(funded: Any, operator: Any) -> None:
    wallet_service.adjust(
        funded,
        kind=str(EntryKind.BONUS),
        amount=Decimal("50"),
        reference="adjustment:welcome",
        reason="Welcome bonus",
        by=operator,
    )

    balance = wallet_service.balance(funded)
    assert balance["settled"] == Decimal("1050.0000")
    assert balance["available"] == Decimal("1050.0000")


def test_an_adjustment_records_who_made_it_and_why(funded: Any, operator: Any) -> None:
    written = wallet_service.adjust(
        funded,
        kind=str(EntryKind.ADJUSTMENT_CREDIT),
        amount=Decimal("5"),
        reference="adjustment:goodwill",
        reason="Goodwill for a late payout",
        by=operator,
    )

    entry = wallet_service.entry(funded, written["id"])
    assert entry["metadata"]["reason"] == "Goodwill for a late payout"
    assert entry["metadata"]["adjusted_by"] == str(operator.pk)


def test_an_unexplained_adjustment_is_refused(funded: Any) -> None:
    with pytest.raises(WalletError, match="needs a reason"):
        wallet_service.adjust(
            funded,
            kind=str(EntryKind.BONUS),
            amount=Decimal("5"),
            reference="adjustment:silent",
            reason="   ",
        )


def test_a_deposit_is_not_something_the_back_office_writes_by_hand(funded: Any) -> None:
    with pytest.raises(WalletError, match="not something the back office writes"):
        wallet_service.adjust(
            funded,
            kind=str(EntryKind.DEPOSIT),
            amount=Decimal("5"),
            reference="adjustment:sneaky",
            reason="Trying to conjure a deposit",
        )


def test_a_debit_adjustment_past_the_balance_is_refused(funded: Any) -> None:
    with pytest.raises(WalletError):
        wallet_service.adjust(
            funded,
            kind=str(EntryKind.ADJUSTMENT_DEBIT),
            amount=Decimal("1001"),
            reference="adjustment:clawback",
            reason="Clawing back more than is there",
        )

    assert wallet_service.balance(funded)["settled"] == Decimal("1000.0000")


def test_the_same_adjustment_reference_is_written_once(funded: Any) -> None:
    first = wallet_service.adjust(
        funded,
        kind=str(EntryKind.BONUS),
        amount=Decimal("10"),
        reference="adjustment:once",
        reason="Bonus",
    )
    again = wallet_service.adjust(
        funded,
        kind=str(EntryKind.BONUS),
        amount=Decimal("10"),
        reference="adjustment:once",
        reason="Bonus",
    )

    assert first["id"] == again["id"]
    assert wallet_service.balance(funded)["settled"] == Decimal("1010.0000")


def test_paying_inside_the_project_spends_the_balance(funded: Any) -> None:
    wallet_service.pay(funded, amount=Decimal("250"), reference="order:1", description="Order #1")

    assert wallet_service.balance(funded)["settled"] == Decimal("750.0000")


def test_paying_more_than_is_there_is_refused(funded: Any) -> None:
    with pytest.raises(WalletError):
        wallet_service.pay(funded, amount=Decimal("2000"), reference="order:2")

    assert wallet_service.balance(funded)["settled"] == Decimal("1000.0000")


def test_a_frozen_wallet_takes_money_in_and_refuses_to_pay_out(
    funded: Any, card: PaymentMethod, operator: Any
) -> None:
    wallet_service.set_wallet_status(
        _wallet(funded).pk, str(WalletStatus.FROZEN), by=operator, reason="Compliance hold"
    )

    wallet_service.deposit(funded, amount=Decimal("10"), method="cash", reference="in")
    with pytest.raises(WalletError):
        wallet_service.withdraw(
            funded, amount=Decimal("10"), method="card", reference="out", destination="4242"
        )


def test_closing_a_wallet_that_still_holds_money_is_refused(funded: Any) -> None:
    with pytest.raises(InvalidTransition, match="still holds"):
        wallet_service.set_wallet_status(_wallet(funded).pk, str(WalletStatus.CLOSED))


def test_closing_a_wallet_with_a_pending_movement_is_refused(
    funded: Any, card: PaymentMethod
) -> None:
    wallet_service.withdraw(
        funded, amount=Decimal("1000"), method="card", reference="out", destination="4242"
    )

    with pytest.raises(InvalidTransition, match="still pending"):
        wallet_service.set_wallet_status(_wallet(funded).pk, str(WalletStatus.CLOSED))


def test_an_empty_wallet_closes_and_stays_closed(alice: Any, cash: PaymentMethod) -> None:
    wallet_service.balance(alice)
    wallet = _wallet(alice)

    wallet_service.set_wallet_status(wallet.pk, str(WalletStatus.CLOSED))
    assert _wallet(alice).status == str(WalletStatus.CLOSED)

    with pytest.raises(InvalidTransition, match="stays closed"):
        wallet_service.set_wallet_status(wallet.pk, str(WalletStatus.ACTIVE))


def test_a_status_that_is_not_one_is_refused(funded: Any) -> None:
    with pytest.raises(WalletError, match="not a wallet status"):
        wallet_service.set_wallet_status(_wallet(funded).pk, "asleep")
