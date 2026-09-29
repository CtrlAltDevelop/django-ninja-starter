"""Edges that used to reach the database, or the rail, before anything said no.

Each test here is one way in that skipped a check the ordinary path makes: a
view-only admin running a money action, a payout leaving a frozen wallet, an
amount no column can hold, text longer than its column, a reason that went
around the metadata cap, a quote that promised what recording then refused.
"""

from decimal import Decimal
from typing import Any

import pytest
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.test import Client, override_settings
from django.urls import reverse

from apps.wallet.adminui import wallet_numbers
from apps.wallet.catalog import ExchangeRate, PaymentMethod
from apps.wallet.errors import InvalidAmount, WalletError, WalletFrozen
from apps.wallet.models import EntryStatus, Wallet, WalletEntry, WalletStatus
from apps.wallet.services import wallet_service

pytestmark = pytest.mark.django_db


def _freeze(user: Any) -> None:
    wallet = wallet_service.wallet_for(user)
    wallet_service.set_wallet_status(wallet.pk, str(WalletStatus.FROZEN))


def test_a_view_only_staff_user_cannot_run_money_actions(alice: Any, card: PaymentMethod) -> None:
    """Viewing movements is not settling them; the action POST changes nothing."""
    entry = wallet_service.deposit(alice, amount=Decimal("100"), method="card", reference="r")
    viewer = get_user_model().objects.create_user(
        username="viewer", email="viewer@example.test", password="x", is_staff=True
    )
    viewer.user_permissions.add(
        Permission.objects.get(codename="view_walletentry", content_type__app_label="wallet"),
        Permission.objects.get(codename="view_wallet", content_type__app_label="wallet"),
    )
    client = Client()
    client.force_login(viewer)
    client.post(
        reverse("admin:wallet_walletentry_changelist"),
        {"action": "settle_entries", "_selected_action": [str(entry["id"])]},
    )
    client.post(
        reverse("admin:wallet_wallet_changelist"),
        {
            "action": "freeze_wallets",
            "_selected_action": [str(wallet_service.wallet_for(alice).pk)],
        },
    )
    assert WalletEntry.objects.get(pk=entry["id"]).status == str(EntryStatus.PENDING)
    assert Wallet.objects.get(user=alice).status == str(WalletStatus.ACTIVE)


def test_a_frozen_wallet_cannot_have_a_payout_approved(funded: Any, counter: PaymentMethod) -> None:
    entry = wallet_service.withdraw(funded, amount=Decimal("10"), method="counter", reference="w")
    _freeze(funded)
    with pytest.raises(WalletFrozen):
        wallet_service.approve(entry["id"])
    assert WalletEntry.objects.get(pk=entry["id"]).status == str(EntryStatus.PENDING)


def test_a_frozen_wallet_cannot_have_a_payout_settled_by_an_operator(
    funded: Any, card: PaymentMethod
) -> None:
    entry = wallet_service.withdraw(
        funded, amount=Decimal("10"), method="card", reference="w", destination="card_1"
    )
    _freeze(funded)
    with pytest.raises(WalletFrozen):
        wallet_service.settle(funded, entry["id"])


@pytest.mark.parametrize("amount", ["NaN", "sNaN", "Infinity", "-Infinity", "1e40", "1e14"])
def test_amounts_no_column_can_hold_are_refused(funded: Any, bob: Any, amount: str) -> None:
    with pytest.raises(InvalidAmount):
        wallet_service.deposit(funded, amount=Decimal(amount), method="cash", reference="d")
    with pytest.raises(InvalidAmount):
        wallet_service.transfer(funded, to_user=bob, amount=Decimal(amount), reference="t")


def test_text_longer_than_its_column_is_refused(funded: Any) -> None:
    with pytest.raises(WalletError, match="reference"):
        wallet_service.deposit(funded, amount=Decimal("1"), method="cash", reference="r" * 121)


def test_a_cancel_reason_cannot_go_around_the_metadata_cap(alice: Any, card: PaymentMethod) -> None:
    entry = wallet_service.deposit(alice, amount=Decimal("100"), method="card", reference="r")
    with pytest.raises(WalletError):
        wallet_service.cancel(alice, entry["id"], reason="x" * 10_000)
    assert WalletEntry.objects.get(pk=entry["id"]).status == str(EntryStatus.PENDING)


@override_settings(WALLET_MAX_WITHDRAWAL="500")
def test_a_quote_checks_the_deployment_limits(funded: Any) -> None:
    with pytest.raises(InvalidAmount):
        wallet_service.quote(funded, method="cash", direction="debit", amount=Decimal("800"))


def test_rates_return_only_the_newest_row_per_pair(eur_rate: ExchangeRate) -> None:
    ExchangeRate.objects.create(base="EUR", quote="USD", rate=Decimal("1.20"), source="test")
    rates = wallet_service.rates()
    assert [rate["rate"] for rate in rates] == [Decimal("1.20")]


def test_dashboard_totals_are_per_currency(alice: Any, bob: Any, counter: PaymentMethod) -> None:
    wallet_service.deposit(alice, amount=Decimal("100"), method="counter", reference="a")
    euro = Wallet.objects.create(user=bob, currency="EUR")
    WalletEntry.objects.create(
        wallet=euro,
        kind="deposit",
        method="cash",
        payment_method=counter,
        amount=Decimal("50"),
        gross_amount=Decimal("50"),
        status=str(EntryStatus.PENDING),
        approval="requested",
        reference="b",
    )
    assert wallet_numbers()["waiting_value"] == "50.00 EUR + 100.00 USD"
