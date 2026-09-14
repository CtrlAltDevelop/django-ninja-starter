"""What the wallet announces, and the rule that it announces only what happened.

A receiver here is somebody else's code -- a notification, a payout integration
-- so the two properties worth pinning down are the ones that would hurt it: a
signal must never fire for a movement that was rolled back, and a receiver that
raises must not undo the money that already moved.
"""

from decimal import Decimal
from typing import Any

import pytest
from django.dispatch import Signal

from apps.wallet import signals
from apps.wallet.catalog import PaymentMethod
from apps.wallet.errors import InsufficientFunds, InvalidTransition
from apps.wallet.services import wallet_service

pytestmark = pytest.mark.django_db


class Heard:
    """Every payload one signal delivered, in order."""

    def __init__(self, signal: Signal) -> None:
        self.payloads: list[dict[str, Any]] = []
        self.signal = signal

    def __call__(self, sender: Any, **payload: Any) -> None:
        self.payloads.append(payload)

    def __enter__(self) -> "Heard":
        self.signal.connect(self, weak=False)
        return self

    def __exit__(self, *exc: Any) -> None:
        self.signal.disconnect(self)


def test_a_settled_deposit_is_announced_once_it_commits(
    alice: Any, cash: PaymentMethod, django_capture_on_commit_callbacks: Any
) -> None:
    with (
        Heard(signals.entry_settled) as settled,
        Heard(signals.entry_recorded) as recorded,
        django_capture_on_commit_callbacks(execute=True),
    ):
        entry = wallet_service.deposit(alice, amount=Decimal("10"), method="cash", reference="d")

    assert [payload["entry"]["id"] for payload in recorded.payloads] == [entry["id"]]
    assert [payload["entry"]["status"] for payload in settled.payloads] == ["done"]


def test_nothing_is_announced_for_a_movement_that_was_rolled_back(
    funded: Any, card: PaymentMethod, django_capture_on_commit_callbacks: Any
) -> None:
    """The settlement is refused a line after it was queued, so nobody hears of it."""
    payout = wallet_service.withdraw(
        funded, amount=Decimal("900"), method="card", reference="w", destination="4242"
    )
    wallet_service.withdraw(funded, amount=Decimal("100"), method="cash", reference="drain")
    wallet_service.reverse(funded, _seed(funded), reference="cb")

    with (
        Heard(signals.entry_settled) as settled,
        django_capture_on_commit_callbacks(execute=True),
        pytest.raises(InsufficientFunds),
    ):
        wallet_service.settle(funded, payout["id"])

    assert settled.payloads == []


def test_a_cleared_payout_is_ready_to_send(
    funded: Any, card: PaymentMethod, django_capture_on_commit_callbacks: Any
) -> None:
    """The hook a payout integration listens on, carrying where the money goes."""
    with (
        Heard(signals.payout_ready) as ready,
        django_capture_on_commit_callbacks(execute=True),
    ):
        payout = wallet_service.withdraw(
            funded, amount=Decimal("100"), method="card", reference="w", destination="4242"
        )

    assert [payload["entry"]["id"] for payload in ready.payloads] == [payout["id"]]
    assert ready.payloads[0]["entry"]["destination"] == "4242"


def test_a_payout_awaiting_approval_is_ready_only_once_applied(
    funded: Any,
    counter: PaymentMethod,
    card: PaymentMethod,
    django_capture_on_commit_callbacks: Any,
) -> None:
    card.requires_approval = True
    card.save()

    with Heard(signals.payout_ready) as ready:
        with django_capture_on_commit_callbacks(execute=True):
            payout = wallet_service.withdraw(
                funded, amount=Decimal("100"), method="card", reference="w", destination="4242"
            )
        assert ready.payloads == []

        with django_capture_on_commit_callbacks(execute=True):
            wallet_service.approve(payout["id"])

    assert [payload["entry"]["id"] for payload in ready.payloads] == [payout["id"]]


def test_a_reversal_announces_both_halves(
    alice: Any, cash: PaymentMethod, django_capture_on_commit_callbacks: Any
) -> None:
    deposit = wallet_service.deposit(alice, amount=Decimal("10"), method="cash", reference="d")

    with (
        Heard(signals.entry_reversed) as heard,
        django_capture_on_commit_callbacks(execute=True),
    ):
        correction = wallet_service.reverse_entry(deposit["id"], reason="returned")

    (payload,) = heard.payloads
    assert payload["entry"]["status"] == "reversed"
    assert payload["correction"]["id"] == correction["id"]


def test_a_receiver_that_raises_does_not_undo_the_money(
    alice: Any, cash: PaymentMethod, django_capture_on_commit_callbacks: Any
) -> None:
    def broken(sender: Any, **payload: Any) -> None:
        raise RuntimeError("the notification service is down")

    signals.entry_settled.connect(broken, weak=False)
    try:
        with django_capture_on_commit_callbacks(execute=True):
            wallet_service.deposit(alice, amount=Decimal("10"), method="cash", reference="d")
    finally:
        signals.entry_settled.disconnect(broken)

    assert wallet_service.balance(alice)["settled"] == Decimal("10.0000")


def test_one_half_of_a_transfer_cannot_be_reversed(
    funded: Any, bob: Any, cash: PaymentMethod
) -> None:
    """Crediting the sender back while the recipient keeps the money would create money."""
    sent = wallet_service.transfer(funded, to_user=bob, amount=Decimal("50"), reference="t")

    with pytest.raises(InvalidTransition):
        wallet_service.reverse_entry(sent["id"])
    with pytest.raises(InvalidTransition):
        wallet_service.reverse_entry(sent["counterparty_id"])

    assert wallet_service.balance(funded)["settled"] == Decimal("950.0000")
    assert wallet_service.balance(bob)["settled"] == Decimal("50.0000")


def _seed(user: Any) -> Any:
    return next(row["id"] for row in wallet_service.entries(user) if row["reference"] == "seed")
