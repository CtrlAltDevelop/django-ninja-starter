"""Two arithmetic rules a wallet cannot get wrong, and used to.

**A pending payout must not be charged for its own hold.** The balance check at
settlement ran against ``available``, which already has the pending withdrawal
subtracted from it -- so the entry was counted twice and a customer who asked to
withdraw their whole balance could never be paid. The recommended configuration,
where every payout waits for an operator, was the one that hit it.

**A reference must only ever mean one thing.** It is what makes a retry safe, and
it can only do that if a second call carrying it is checked against the first.
"""

from decimal import Decimal
from typing import Any

import pytest
from django.contrib.auth import get_user_model

from apps.wallet.catalog import MethodCurrency, PaymentMethod
from apps.wallet.errors import InsufficientFunds, ReferenceReused
from apps.wallet.services import wallet_service

pytestmark = pytest.mark.django_db


@pytest.fixture
def zoe(db: None) -> Any:
    return get_user_model().objects.create_user(username="zoe", email="z@example.test")


def _method(code: str, kind: str, *, approval: bool = False) -> PaymentMethod:
    method = PaymentMethod.objects.create(
        code=code,
        name=code,
        rail=kind,
        is_enabled=True,
        supports_deposit=True,
        supports_withdrawal=True,
        requires_approval=approval,
    )
    MethodCurrency.objects.create(method=method, currency="USD", is_enabled=True)
    return method


@pytest.fixture
def counter(db: None) -> PaymentMethod:
    """Settles at once, so a wallet can be funded in one call."""
    return _method("counter", "cash")


@pytest.fixture
def slow(db: None) -> PaymentMethod:
    """Waits on a processor, so a payout is pending between recording and paying."""
    return _method("slow", "bank_transfer")


def _fund(user: Any, amount: str) -> None:
    wallet_service.deposit(user, amount=Decimal(amount), method="counter", reference=f"in-{amount}")


# -- the settlement re-check --------------------------------------------------


def test_a_payout_of_the_whole_balance_can_actually_be_paid(
    zoe: Any, counter: PaymentMethod, slow: PaymentMethod
) -> None:
    """The case the double-count broke, and the commonest one there is."""
    _fund(zoe, "1000")
    entry = wallet_service.withdraw(
        zoe, amount=Decimal("1000"), method="slow", reference="all-of-it", destination="GB00"
    )
    assert entry["status"] == "pending"

    settled = wallet_service.settle(zoe, entry["id"], external_reference="bank-ok")

    assert settled["status"] == "done"
    assert wallet_service.balance(zoe)["settled"] == Decimal("0.0000")


def test_approving_a_payout_of_the_whole_balance_pays_it(zoe: Any, counter: PaymentMethod) -> None:
    """The same sum down the operator's path, which is the recommended one."""
    supervised = _method("supervised", "bank_transfer", approval=True)
    assert supervised.requires_approval
    _fund(zoe, "1000")
    entry = wallet_service.withdraw(
        zoe, amount=Decimal("1000"), method="supervised", reference="all-of-it", destination="GB00"
    )

    applied = wallet_service.approve(entry["id"])
    settled = wallet_service.settle(zoe, applied["id"])

    assert settled["status"] == "done"
    assert wallet_service.balance(zoe)["settled"] == Decimal("0.0000")


def test_the_re_check_still_refuses_a_payout_the_money_left(
    zoe: Any, counter: PaymentMethod, slow: PaymentMethod
) -> None:
    """The point of re-checking at all: handing back its own hold is not a waiver.

    A payout is recorded while the money is there, and something else takes the
    money before the rail confirms. Settling anyway is the one mistake a wallet
    cannot undo, so it is still refused -- this is what distinguishes the fix
    from simply deleting the check.
    """
    _fund(zoe, "1000")
    payout = wallet_service.withdraw(
        zoe, amount=Decimal("600"), method="slow", reference="first", destination="GB00"
    )
    # A chargeback takes the deposit away while the payout is in flight.
    wallet_service.reverse(zoe, _deposit_id(zoe), reference="chargeback")

    with pytest.raises(InsufficientFunds):
        wallet_service.settle(zoe, payout["id"])


def test_two_pending_payouts_still_cannot_both_be_paid(
    zoe: Any, counter: PaymentMethod, slow: PaymentMethod
) -> None:
    """Each may be settled against its own hold, and not against the other's."""
    _fund(zoe, "1000")
    first = wallet_service.withdraw(
        zoe, amount=Decimal("700"), method="slow", reference="one", destination="GB00"
    )
    second = wallet_service.withdraw(
        zoe, amount=Decimal("300"), method="slow", reference="two", destination="GB00"
    )

    wallet_service.settle(zoe, first["id"])
    wallet_service.settle(zoe, second["id"])

    assert wallet_service.balance(zoe)["settled"] == Decimal("0.0000")


def test_a_third_payout_beyond_the_balance_is_refused_at_recording(
    zoe: Any, counter: PaymentMethod, slow: PaymentMethod
) -> None:
    """The hold still holds: what is promised is not available to promise again."""
    _fund(zoe, "1000")
    wallet_service.withdraw(
        zoe, amount=Decimal("1000"), method="slow", reference="one", destination="GB00"
    )

    with pytest.raises(InsufficientFunds):
        wallet_service.withdraw(
            zoe, amount=Decimal("1"), method="slow", reference="two", destination="GB00"
        )


def _deposit_id(user: Any) -> Any:
    from apps.wallet.models import EntryKind, WalletEntry

    return (
        WalletEntry.objects.filter(wallet__user=user, kind=str(EntryKind.DEPOSIT))
        .values_list("pk", flat=True)
        .first()
    )


# -- the reference ------------------------------------------------------------


def test_the_same_reference_for_the_same_request_is_the_retry_it_claims_to_be(
    zoe: Any, counter: PaymentMethod
) -> None:
    """Which is the whole purpose, and must keep working."""
    first = wallet_service.deposit(zoe, amount=Decimal("50"), method="counter", reference="same")
    again = wallet_service.deposit(zoe, amount=Decimal("50"), method="counter", reference="same")

    assert first["id"] == again["id"]
    assert wallet_service.balance(zoe)["settled"] == Decimal("50.0000")


def test_the_same_reference_for_a_different_amount_is_refused(
    zoe: Any, counter: PaymentMethod
) -> None:
    """It used to answer 200 with the old entry: five thousand recorded as five."""
    wallet_service.deposit(zoe, amount=Decimal("5"), method="counter", reference="same")

    with pytest.raises(ReferenceReused):
        wallet_service.deposit(zoe, amount=Decimal("5000"), method="counter", reference="same")


def test_the_same_reference_for_a_different_direction_is_refused(
    zoe: Any, counter: PaymentMethod
) -> None:
    """The worst shape of it: a withdrawal answered with a deposit's receipt."""
    _fund(zoe, "1000")
    wallet_service.deposit(zoe, amount=Decimal("50"), method="counter", reference="same")

    with pytest.raises(ReferenceReused):
        wallet_service.withdraw(
            zoe, amount=Decimal("50"), method="counter", reference="same", destination="GB00"
        )


def test_the_same_reference_through_a_different_method_is_refused(
    zoe: Any, counter: PaymentMethod, slow: PaymentMethod
) -> None:
    wallet_service.deposit(zoe, amount=Decimal("50"), method="counter", reference="same")

    with pytest.raises(ReferenceReused):
        wallet_service.deposit(zoe, amount=Decimal("50"), method="slow", reference="same")


def test_one_account_s_reference_does_not_collide_with_another_s(
    zoe: Any, counter: PaymentMethod
) -> None:
    """References are the client's, and two clients have no way to agree on them."""
    other = get_user_model().objects.create_user(username="yan", email="y@example.test")

    mine = wallet_service.deposit(zoe, amount=Decimal("5"), method="counter", reference="r1")
    theirs = wallet_service.deposit(other, amount=Decimal("99"), method="counter", reference="r1")

    assert mine["id"] != theirs["id"]
    assert wallet_service.balance(other)["settled"] == Decimal("99.0000")
