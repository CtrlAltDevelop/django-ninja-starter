"""Paying an order from the wallet balance -- the only way, when the wallet is in."""

from decimal import Decimal
from typing import Any

import pytest
from django.apps import apps as django_apps
from django.test import override_settings

from apps.shop.models import Address, Order, OrderStatus, PaymentStatus, Product, ShippingMethod
from apps.shop.services import ShopRefused, shop_service

pytestmark = [
    pytest.mark.django_db,
    pytest.mark.skipif(not django_apps.is_installed("apps.wallet"), reason="needs the wallet app"),
]


@pytest.fixture(autouse=True)
def _pay_from_wallet() -> Any:
    with override_settings(SHOP_PAYMENT_PROVIDER="", WALLET_CURRENCY="USD", SHOP_CURRENCY="USD"):
        yield


@pytest.fixture
def placed(alice: Any, laptop: Product, address: Address, shipping: ShippingMethod) -> Order:
    shop_service.add_to_cart(alice, "featherbook-14")
    return shop_service.checkout(alice, address_id=address.pk, shipping_method_id=shipping.pk)


def _credit(user: Any, amount: Decimal) -> None:
    from apps.wallet.services import wallet_service

    wallet_service.adjust(
        user, kind="adjustment_credit", amount=amount, reference="seed", reason="test"
    )


def _available(user: Any) -> Decimal:
    from apps.wallet.services import wallet_service

    return Decimal(str(wallet_service.balance(user)["available"]))


def test_an_order_is_opened_against_the_wallet(placed: Order) -> None:
    assert placed.payments.get().provider == "wallet"


def test_paying_takes_the_total_from_the_balance(alice: Any, placed: Order) -> None:
    _credit(alice, placed.total + 10)

    paid = shop_service.pay_order(alice, placed.number)

    assert paid.status == OrderStatus.PAID
    assert _available(alice) == Decimal("10")
    assert placed.payments.get().status == PaymentStatus.SUCCEEDED


def test_paying_twice_takes_the_money_once(alice: Any, placed: Order) -> None:
    _credit(alice, placed.total * 3)

    shop_service.pay_order(alice, placed.number)
    shop_service.pay_order(alice, placed.number)

    assert _available(alice) == placed.total * 2


def test_a_balance_that_does_not_cover_it_is_refused(alice: Any, placed: Order) -> None:
    _credit(alice, placed.total - 1)

    with pytest.raises(ShopRefused):
        shop_service.pay_order(alice, placed.number)

    placed.refresh_from_db()
    assert placed.status == OrderStatus.PENDING
    assert _available(alice) == placed.total - 1


def test_nobody_can_mark_a_wallet_order_paid_by_hand(placed: Order) -> None:
    with pytest.raises(ShopRefused):
        shop_service.settle_order(placed)


def test_another_account_cannot_pay_it(bob: Any, placed: Order) -> None:
    from apps.shop.services import ShopNotFound

    _credit(bob, placed.total)
    with pytest.raises(ShopNotFound):
        shop_service.pay_order(bob, placed.number)


def test_a_refund_puts_the_money_back_in_the_wallet(alice: Any, placed: Order) -> None:
    _credit(alice, placed.total)
    shop_service.pay_order(alice, placed.number)

    shop_service.advance_order(placed, str(OrderStatus.REFUNDED))

    assert _available(alice) == placed.total


def test_it_is_published_over_http(alice: Any, placed: Order, client: Any) -> None:
    from apps.shop.tests.conftest import bearer

    _credit(alice, placed.total)
    response = client.post(f"/api/v1/shop/orders/{placed.number}/pay", **bearer(alice))

    assert response.status_code == 200, response.content
    assert response.json()["data"]["status"] == "paid"
