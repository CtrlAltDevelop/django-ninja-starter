"""Who did it, how much they may write, and who may be paid.

The three things a code review found missing rather than wrong. Settling is
where a row becomes money, so the operator behind it belongs on the entry;
`metadata` is filled in by the caller, so it needs a ceiling; and a transfer to
an account that can no longer sign in is money that stops.
"""

from decimal import Decimal
from typing import Any

import pytest
from django.contrib.admin.sites import AdminSite
from django.contrib.auth import get_user_model

from apps.wallet.admin import WalletEntryAdmin
from apps.wallet.catalog import PaymentMethod
from apps.wallet.errors import WalletError
from apps.wallet.models import WalletEntry
from apps.wallet.services import wallet_service
from apps.wallet.tests.test_admin import admin_request

pytestmark = pytest.mark.django_db


@pytest.fixture
def operator_account(db: None) -> Any:
    """Somebody with the back office's own screens, for the actions below."""
    return get_user_model().objects.create_superuser(
        username="auditor", email="auditor@example.test", password="x"
    )


def test_settling_from_the_admin_records_the_operator(
    alice: Any, card: PaymentMethod, operator_account: Any
) -> None:
    """The one action that turns a row into money says who said so.

    Through a rail that waits, because a method that settles on approval is
    already attributed by `reviewed_by` -- the approval *was* the settlement.
    """
    entry = wallet_service.deposit(alice, amount=Decimal("100"), method="card", reference="d")
    screen = WalletEntryAdmin(WalletEntry, AdminSite())

    screen.settle_entries(
        admin_request(operator_account), WalletEntry.objects.filter(pk=entry["id"])
    )

    settled = WalletEntry.objects.get(pk=entry["id"])
    assert settled.status == "done"
    assert settled.metadata["settled_by_operator"] == str(operator_account.pk)
    assert "settled_by_operator" not in wallet_service.entry(alice, entry["id"])["metadata"]


def test_failing_from_the_admin_records_the_operator(
    alice: Any, card: PaymentMethod, operator_account: Any
) -> None:
    entry = wallet_service.deposit(alice, amount=Decimal("100"), method="card", reference="d")
    screen = WalletEntryAdmin(WalletEntry, AdminSite())

    screen.fail_entries(admin_request(operator_account), WalletEntry.objects.filter(pk=entry["id"]))

    metadata = WalletEntry.objects.get(pk=entry["id"]).metadata
    assert metadata["failed_by_operator"] == str(operator_account.pk)


def test_expiring_from_the_admin_records_the_operator(
    alice: Any, card: PaymentMethod, operator_account: Any
) -> None:
    entry = wallet_service.deposit(alice, amount=Decimal("100"), method="card", reference="d")
    screen = WalletEntryAdmin(WalletEntry, AdminSite())

    screen.expire_entries(
        admin_request(operator_account), WalletEntry.objects.filter(pk=entry["id"])
    )

    metadata = WalletEntry.objects.get(pk=entry["id"]).metadata
    assert metadata["expired_by_operator"] == str(operator_account.pk)


def test_a_rail_confirmation_records_no_operator(alice: Any, cash: PaymentMethod) -> None:
    """Nobody in the back office settled it, and the entry does not pretend otherwise."""
    entry = wallet_service.deposit(alice, amount=Decimal("100"), method="cash", reference="d")

    assert "settled_by_operator" not in wallet_service.entry(alice, entry["id"])["metadata"]


def test_an_account_cancelling_its_own_movement_is_not_recorded_as_an_operator(
    alice: Any, counter: PaymentMethod
) -> None:
    request = wallet_service.deposit(alice, amount=Decimal("50"), method="counter", reference="r")

    wallet_service.cancel(alice, request["id"], reason="Changed my mind.")

    metadata = wallet_service.entry(alice, request["id"])["metadata"]
    assert "cancelled_by_operator" not in metadata
    assert metadata["reason"] == "Changed my mind."


def test_metadata_larger_than_the_cap_is_refused(
    alice: Any, cash: PaymentMethod, settings: Any
) -> None:
    """The field is the caller's, so it has a ceiling like every other caller-set size."""
    settings.WALLET_MAX_METADATA_BYTES = 256

    with pytest.raises(WalletError, match="the most one movement may carry"):
        wallet_service.deposit(
            alice,
            amount=Decimal("10"),
            method="cash",
            reference="fat",
            metadata={"padding": "x" * 500},
        )

    assert wallet_service.entries(alice) == []


def test_metadata_inside_the_cap_is_kept(alice: Any, cash: PaymentMethod, settings: Any) -> None:
    settings.WALLET_MAX_METADATA_BYTES = 256

    entry = wallet_service.deposit(
        alice, amount=Decimal("10"), method="cash", reference="ok", metadata={"order": "A-1"}
    )

    assert entry["metadata"] == {"order": "A-1"}


def test_the_metadata_cap_can_be_turned_off(alice: Any, cash: PaymentMethod, settings: Any) -> None:
    """Zero means a deployment that limits request size at its edge is not counted twice."""
    settings.WALLET_MAX_METADATA_BYTES = 0

    entry = wallet_service.deposit(
        alice,
        amount=Decimal("10"),
        method="cash",
        reference="huge",
        metadata={"padding": "x" * 20000},
    )

    assert len(entry["metadata"]["padding"]) == 20000


def test_a_transfer_to_a_closed_account_is_refused(funded: Any, cash: PaymentMethod) -> None:
    """Money that lands where nobody can sign in is money that stops."""
    gone = get_user_model().objects.create_user(
        username="gone", email="gone@example.test", is_active=False
    )

    with pytest.raises(WalletError, match="could never leave"):
        wallet_service.transfer(funded, to_user=gone, amount=Decimal("10"), reference="t")

    assert wallet_service.balance(funded)["settled"] == Decimal("1000.0000")
