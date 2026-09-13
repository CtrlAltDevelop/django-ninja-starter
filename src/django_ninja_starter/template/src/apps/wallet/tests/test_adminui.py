"""The sidebar group and the three dashboard numbers.

Worth testing rather than eyeballing, because the numbers are the ones an
operator acts on: a queue that under-reports is a customer left waiting, and a
"ways to pay" card that reads reassuringly while nothing is configured hides the
one failure a customer discovers at the last step of paying.
"""

from decimal import Decimal
from typing import Any

import pytest
from django.contrib.auth import get_user_model
from django.test import RequestFactory

from apps.wallet.adminui import dashboard, navigation, wallet_numbers
from apps.wallet.catalog import PaymentMethod
from apps.wallet.services import wallet_service

pytestmark = pytest.mark.django_db


def admin_request(user: Any) -> Any:
    request = RequestFactory().get("/admin/")
    request.user = user
    return request


@pytest.fixture
def superuser(db: None) -> Any:
    return get_user_model().objects.create_superuser(
        username="root", email="root@example.test", password="x"
    )


def test_the_queue_counts_what_is_waiting_on_a_person(alice: Any, counter: PaymentMethod) -> None:
    wallet_service.deposit(alice, amount=Decimal("40"), method="counter", reference="a")
    wallet_service.deposit(alice, amount=Decimal("60"), method="counter", reference="b")

    numbers = wallet_numbers()

    assert numbers["waiting"] == 2
    # Written as money, not at the four decimals amounts are stored at.
    assert numbers["waiting_value"] == "100.00 USD"


def test_pending_payouts_are_counted_apart_from_deposits(
    funded: Any, cash: PaymentMethod, card: PaymentMethod
) -> None:
    """Money already promised, which is out of every available balance."""
    wallet_service.withdraw(
        funded, amount=Decimal("200"), method="card", reference="w", destination="**** 4242"
    )

    numbers = wallet_numbers()

    assert numbers["payouts"] == 1
    assert numbers["payouts_value"] == "200.00 USD"


def test_no_usable_method_reads_as_bad_rather_than_as_zero(superuser: Any, db: None) -> None:
    """A wallet nobody can pay into should say so here, not at a customer's last step."""
    section = dashboard(admin_request(superuser))
    assert section is not None

    methods = next(card for card in section["cards"] if card["label"] == "Ways to pay")
    assert methods["value"] == 0
    assert methods["tone"] == "bad"
    assert "nobody can pay" in methods["hint"]


def test_a_configured_method_turns_that_card_good(superuser: Any, cash: PaymentMethod) -> None:
    section = dashboard(admin_request(superuser))
    assert section is not None

    methods = next(card for card in section["cards"] if card["label"] == "Ways to pay")
    assert methods["value"] == 1
    assert methods["tone"] == "good"


def test_a_method_with_no_currency_does_not_count_as_usable(superuser: Any, db: None) -> None:
    """Enabled and unfinished is not the same as ready, and the card must not say it is."""
    PaymentMethod.objects.create(code="half", name="Half done", rail="cash", is_enabled=True)

    assert wallet_numbers()["methods"] == 0


def test_the_dashboard_is_hidden_from_somebody_who_may_not_read_movements(
    alice: Any, db: None
) -> None:
    assert dashboard(admin_request(alice)) is None


def test_the_sidebar_leads_with_what_is_waiting(superuser: Any, db: None) -> None:
    group = navigation(admin_request(superuser))

    assert group["title"] == "Wallet"
    assert group["items"][0]["title"] == "Movements"


def test_the_waiting_count_ignores_requests_nobody_can_act_on(
    alice: Any, bob: Any, counter: PaymentMethod
) -> None:
    """The card is a call to action, so it has to count only what can be acted on.

    A cancelled request keeps its `requested` approval for the record. Counting
    it here would send an operator to a queue that does not contain it.
    """
    wallet_service.deposit(alice, amount=Decimal("10"), method="counter", reference="a")
    dead = wallet_service.deposit(bob, amount=Decimal("20"), method="counter", reference="b")
    wallet_service.cancel(bob, dead["id"])

    numbers = wallet_numbers()

    assert numbers["waiting"] == 1
    assert numbers["waiting_value"] == "10.00 USD"
