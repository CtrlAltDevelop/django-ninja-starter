"""Giving up on movements nothing confirmed, and the one wait that is not the job's.

Two joint states are worth pinning, because expiry reads status and approval
together: a stale movement waiting on its rail expires, and a stale request
waiting on a person does not.
"""

import io
from datetime import timedelta
from decimal import Decimal
from typing import Any

import pytest
from django.core.management import call_command
from django.utils import timezone

from apps.wallet.catalog import PaymentMethod
from apps.wallet.models import WalletEntry
from apps.wallet.services import wallet_service

pytestmark = pytest.mark.django_db


def _age(entry_id: Any, hours: int) -> None:
    WalletEntry.objects.filter(pk=entry_id).update(
        created_at=timezone.now() - timedelta(hours=hours)
    )


def test_a_stale_payout_expires_and_releases_its_money(
    funded: Any, card: PaymentMethod, settings: Any
) -> None:
    settings.WALLET_EXPIRE_AFTER_HOURS = 24
    payout = wallet_service.withdraw(
        funded, amount=Decimal("400"), method="card", reference="w", destination="4242"
    )
    _age(payout["id"], 25)
    assert wallet_service.balance(funded)["available"] == Decimal("600.0000")

    result = wallet_service.expire_stale()

    assert result.expired == 1
    assert wallet_service.entry(funded, payout["id"])["status"] == "expired"
    assert wallet_service.balance(funded)["available"] == Decimal("1000.0000")


def test_a_movement_inside_the_window_is_left_alone(
    alice: Any, card: PaymentMethod, settings: Any
) -> None:
    settings.WALLET_EXPIRE_AFTER_HOURS = 24
    deposit = wallet_service.deposit(alice, amount=Decimal("50"), method="card", reference="d")
    _age(deposit["id"], 23)

    assert wallet_service.expire_stale().expired == 0
    assert wallet_service.entry(alice, deposit["id"])["status"] == "pending"


def test_a_stale_request_waiting_on_an_operator_is_not_expired(
    alice: Any, counter: PaymentMethod, settings: Any
) -> None:
    """The queue is how somebody is made to look; the job must not empty it."""
    settings.WALLET_EXPIRE_AFTER_HOURS = 24
    request = wallet_service.deposit(alice, amount=Decimal("50"), method="counter", reference="r")
    _age(request["id"], 200)

    result = wallet_service.expire_stale()

    assert (result.expired, result.awaiting_operator) == (0, 1)
    assert wallet_service.entry(alice, request["id"])["awaiting_approval"] is True


def test_an_approved_payout_still_waiting_on_its_rail_does_expire(
    funded: Any, card: PaymentMethod, settings: Any
) -> None:
    """Approved is not the same wait as requested: the person has done their part."""
    settings.WALLET_EXPIRE_AFTER_HOURS = 24
    card.requires_approval = True
    card.save()
    payout = wallet_service.withdraw(
        funded, amount=Decimal("100"), method="card", reference="w", destination="4242"
    )
    wallet_service.approve(payout["id"])
    _age(payout["id"], 48)

    assert wallet_service.expire_stale().expired == 1


def test_expiry_is_off_until_a_window_is_set(alice: Any, card: PaymentMethod) -> None:
    deposit = wallet_service.deposit(alice, amount=Decimal("50"), method="card", reference="d")
    _age(deposit["id"], 10_000)

    stream = io.StringIO()
    call_command("wallet_expire", stdout=stream)

    assert "Expiry is off" in stream.getvalue()
    assert wallet_service.entry(alice, deposit["id"])["status"] == "pending"


def test_the_command_takes_a_one_off_window(alice: Any, card: PaymentMethod) -> None:
    deposit = wallet_service.deposit(alice, amount=Decimal("50"), method="card", reference="d")
    _age(deposit["id"], 3)

    stream = io.StringIO()
    call_command("wallet_expire", "--older-than-hours", "2", stdout=stream)

    assert "Expired 1" in stream.getvalue()
    assert wallet_service.entry(alice, deposit["id"])["status"] == "expired"


def test_a_settled_movement_cannot_expire(alice: Any, cash: PaymentMethod) -> None:
    from apps.wallet.errors import InvalidTransition

    deposit = wallet_service.deposit(alice, amount=Decimal("50"), method="cash", reference="d")

    with pytest.raises(InvalidTransition):
        wallet_service.expire_entry(deposit["id"])
