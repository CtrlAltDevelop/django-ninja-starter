"""Turning a basket into an order, and the stock that moves when it happens.

The three things worth being strict about are here: an order is a snapshot and
never re-prices itself afterwards, stock is reserved once and released once, and
confirming a payment twice settles one order rather than selling twice.
"""

from decimal import Decimal
from typing import Any

import pytest
from django.test import Client, override_settings
from django.utils import timezone

from apps.shop.models import (
    Address,
    Coupon,
    Discount,
    InventoryReservation,
    Order,
    OrderStatus,
    Payment,
    PaymentStatus,
    Product,
    ShippingMethod,
)
from apps.shop.services import ShopNotFound, ShopRefused, shop_service
from apps.shop.tests.conftest import bearer

pytestmark = pytest.mark.django_db

SHOP = "/api/v1/shop"


@pytest.fixture
def client() -> Client:
    return Client()


@pytest.fixture
def basket(alice: Any, laptop: Product) -> Any:
    """One laptop in Alice's basket, ready to be bought."""
    shop_service.add_to_cart(alice, "featherbook-14", quantity=2)
    return alice


def _checkout(user: Any, address: Address, shipping: ShippingMethod, **extra: Any) -> Order:
    return shop_service.checkout(
        user, address_id=address.pk, shipping_method_id=shipping.pk, **extra
    )


class TestCheckout:
    def test_an_empty_basket_cannot_be_bought(
        self, alice: Any, address: Address, shipping: ShippingMethod
    ) -> None:
        with pytest.raises(ShopRefused):
            _checkout(alice, address, shipping)

    def test_an_order_carries_the_prices_from_the_moment_it_was_placed(
        self, basket: Any, address: Address, shipping: ShippingMethod
    ) -> None:
        order = _checkout(basket, address, shipping)

        assert order.subtotal == Decimal("2400.00")
        assert order.total == Decimal("2400.00")
        assert [item.unit_price for item in order.items.all()] == [Decimal("1200.00")]

    def test_shipping_is_free_above_the_threshold(
        self, basket: Any, address: Address, shipping: ShippingMethod
    ) -> None:
        order = _checkout(basket, address, shipping)

        assert order.shipping_total == Decimal("0.00")

    def test_shipping_is_charged_below_the_threshold(
        self, alice: Any, tshirt: Product, address: Address, shipping: ShippingMethod
    ) -> None:
        medium = tshirt.variants.get(sku="TEE-M")
        shop_service.add_to_cart(alice, "plain-tee", variant_id=medium.pk)

        order = _checkout(alice, address, shipping)

        assert order.shipping_total == Decimal("5.00")
        assert order.total == Decimal("25.00")

    def test_a_discounted_line_is_snapshotted_at_the_discounted_price(
        self,
        alice: Any,
        laptop: Product,
        sale: Discount,
        address: Address,
        shipping: ShippingMethod,
    ) -> None:
        shop_service.add_to_cart(alice, "featherbook-14")

        order = _checkout(alice, address, shipping)

        assert order.items.get().unit_price == Decimal("1080.00")

    def test_the_order_does_not_reprice_when_the_sale_ends(
        self,
        alice: Any,
        laptop: Product,
        sale: Discount,
        address: Address,
        shipping: ShippingMethod,
    ) -> None:
        """An order is a record of an agreement, not a live query."""
        shop_service.add_to_cart(alice, "featherbook-14")
        order = _checkout(alice, address, shipping)
        Discount.objects.filter(pk=sale.pk).update(is_active=False)
        order.refresh_from_db()

        assert order.total == Decimal("1080.00")

    def test_the_delivery_address_is_copied_rather_than_pointed_at(
        self, basket: Any, address: Address, shipping: ShippingMethod
    ) -> None:
        """The shopper editing their address book must not rewrite an old order."""
        order = _checkout(basket, address, shipping)
        address.line1 = "Somewhere else entirely"
        address.save()
        order.refresh_from_db()

        assert order.shipping_address["line1"] == "1 Example Street"

    def test_somebody_elses_address_is_not_found(
        self, basket: Any, bob: Any, shipping: ShippingMethod
    ) -> None:
        theirs = Address.objects.create(
            user=bob,
            full_name="Bob Example",
            phone="+441234567891",
            country="GB",
            city="Leeds",
            postal_code="LS1 1AA",
            line1="2 Example Road",
        )

        with pytest.raises(ShopNotFound):
            _checkout(basket, theirs, shipping)

    def test_an_inactive_shipping_method_is_not_found(
        self, basket: Any, address: Address, shipping: ShippingMethod
    ) -> None:
        shipping.is_active = False
        shipping.save()

        with pytest.raises(ShopNotFound):
            _checkout(basket, address, shipping)

    def test_the_basket_is_emptied_by_a_successful_checkout(
        self, basket: Any, address: Address, shipping: ShippingMethod
    ) -> None:
        _checkout(basket, address, shipping)

        assert shop_service.cart(basket)["items"] == []

    def test_stock_is_reserved_the_moment_the_order_exists(
        self, basket: Any, laptop: Product, address: Address, shipping: ShippingMethod
    ) -> None:
        """Not at payment: two shoppers must not both reach a checkout page for
        the last one and both succeed."""
        order = _checkout(basket, address, shipping)
        laptop.refresh_from_db()

        assert laptop.stock == 3
        assert order.reservations.get().quantity == 2

    def test_a_payment_intent_is_created_pending(
        self, basket: Any, address: Address, shipping: ShippingMethod
    ) -> None:
        order = _checkout(basket, address, shipping)
        payment = order.payments.get()

        assert payment.status == PaymentStatus.PENDING
        assert payment.amount == order.total
        assert payment.idempotency_key

    def test_a_basket_the_shop_can_no_longer_fill_is_refused(
        self, basket: Any, laptop: Product, address: Address, shipping: ShippingMethod
    ) -> None:
        """Somebody else bought them between the basket and the button."""
        Product.objects.filter(pk=laptop.pk).update(stock=1)

        with pytest.raises(ShopRefused):
            _checkout(basket, address, shipping)

    def test_nothing_is_written_when_a_checkout_is_refused(
        self, basket: Any, laptop: Product, address: Address, shipping: ShippingMethod
    ) -> None:
        Product.objects.filter(pk=laptop.pk).update(stock=1)

        with pytest.raises(ShopRefused):
            _checkout(basket, address, shipping)

        assert not Order.objects.exists()
        assert shop_service.cart(basket)["item_count"] == 1


class TestTax:
    def test_a_taxed_product_can_be_bought(
        self, alice: Any, laptop: Product, address: Address, shipping: ShippingMethod
    ) -> None:
        """A rate that does not divide cleanly must not fail the write.

        Nine percent of 1200 is 108 exactly, but the arithmetic that gets there
        goes through a division, and a `Decimal` that arrives at the column with
        four decimal places is refused by the field rather than rounded.
        """
        laptop.tax_rate = Decimal("9.00")
        laptop.save()
        shop_service.add_to_cart(alice, "featherbook-14")

        order = _checkout(alice, address, shipping)

        assert order.tax_total == Decimal("108.00")
        # Shipping is free above the threshold, so the total is subtotal + tax.
        assert order.total == Decimal("1308.00")

    def test_a_rate_that_does_not_divide_cleanly_is_rounded_to_the_cent(
        self, alice: Any, tshirt: Product, address: Address, shipping: ShippingMethod
    ) -> None:
        tshirt.tax_rate = Decimal("7.50")
        tshirt.save()
        medium = tshirt.variants.get(sku="TEE-M")
        shop_service.add_to_cart(alice, "plain-tee", variant_id=medium.pk, quantity=3)

        order = _checkout(alice, address, shipping)

        assert order.tax_total == Decimal("4.50")
        assert order.tax_total.as_tuple().exponent == -2


class TestCoupons:
    def test_a_coupon_comes_off_the_subtotal(
        self, basket: Any, address: Address, shipping: ShippingMethod, coupon: Coupon
    ) -> None:
        order = _checkout(basket, address, shipping, coupon_code="WELCOME")

        assert order.coupon_discount == Decimal("120.00")
        assert order.total == Decimal("2280.00")

    def test_a_coupon_is_matched_whatever_case_it_was_typed_in(
        self, basket: Any, address: Address, shipping: ShippingMethod, coupon: Coupon
    ) -> None:
        order = _checkout(basket, address, shipping, coupon_code="welcome")

        assert order.coupon == coupon

    def test_an_unknown_coupon_is_refused_rather_than_ignored(
        self, basket: Any, address: Address, shipping: ShippingMethod
    ) -> None:
        """Silently ignoring it charges a shopper the price they did not agree to."""
        with pytest.raises(ShopRefused):
            _checkout(basket, address, shipping, coupon_code="NOPE")

    def test_a_coupon_below_its_minimum_is_refused(
        self, alice: Any, tshirt: Product, address: Address, shipping: ShippingMethod
    ) -> None:
        Coupon.objects.create(
            code="BIGSPEND", percent=Decimal("10"), minimum_subtotal=Decimal("500")
        )
        medium = tshirt.variants.get(sku="TEE-M")
        shop_service.add_to_cart(alice, "plain-tee", variant_id=medium.pk)

        with pytest.raises(ShopRefused):
            _checkout(alice, address, shipping, coupon_code="BIGSPEND")

    def test_an_expired_coupon_is_refused(
        self, basket: Any, address: Address, shipping: ShippingMethod
    ) -> None:
        Coupon.objects.create(
            code="LASTYEAR",
            percent=Decimal("50"),
            ends_at=timezone.now() - timezone.timedelta(days=1),
        )

        with pytest.raises(ShopRefused):
            _checkout(basket, address, shipping, coupon_code="LASTYEAR")

    def test_a_coupon_at_its_usage_limit_is_refused(
        self, basket: Any, address: Address, shipping: ShippingMethod
    ) -> None:
        spent = Coupon.objects.create(code="ONCE", percent=Decimal("10"), usage_limit=1)
        Coupon.objects.filter(pk=spent.pk).update(used_count=1)

        with pytest.raises(ShopRefused):
            _checkout(basket, address, shipping, coupon_code="ONCE")

    def test_using_one_counts_against_its_limit(
        self, basket: Any, address: Address, shipping: ShippingMethod, coupon: Coupon
    ) -> None:
        _checkout(basket, address, shipping, coupon_code="WELCOME")
        coupon.refresh_from_db()

        assert coupon.used_count == 1

    def test_an_amount_coupon_never_takes_more_than_the_basket_holds(
        self, alice: Any, tshirt: Product, address: Address, shipping: ShippingMethod
    ) -> None:
        Coupon.objects.create(code="HUGE", amount=Decimal("1000"))
        medium = tshirt.variants.get(sku="TEE-M")
        shop_service.add_to_cart(alice, "plain-tee", variant_id=medium.pk)

        order = _checkout(alice, address, shipping, coupon_code="HUGE")

        assert order.coupon_discount == Decimal("20.00")
        assert order.total >= Decimal("0")


class TestPayment:
    def test_confirming_a_payment_marks_the_order_paid(
        self, basket: Any, address: Address, shipping: ShippingMethod
    ) -> None:
        order = _checkout(basket, address, shipping)

        settled = shop_service.confirm_payment(basket, order.number, reference="psp_1")

        assert settled.status == OrderStatus.PAID
        assert settled.payments.get().provider_reference == "psp_1"
        assert settled.payments.get().paid_at is not None

    def test_confirming_twice_settles_one_order(
        self, basket: Any, laptop: Product, address: Address, shipping: ShippingMethod
    ) -> None:
        """A provider that retries its webhook must not sell the stock twice."""
        order = _checkout(basket, address, shipping)
        shop_service.confirm_payment(basket, order.number)
        shop_service.confirm_payment(basket, order.number)
        laptop.refresh_from_db()

        assert laptop.sales_count == 42
        assert Payment.objects.filter(status=PaymentStatus.SUCCEEDED).count() == 1

    def test_paying_records_the_sale_against_the_product(
        self, basket: Any, laptop: Product, address: Address, shipping: ShippingMethod
    ) -> None:
        order = _checkout(basket, address, shipping)
        shop_service.confirm_payment(basket, order.number)
        laptop.refresh_from_db()

        assert laptop.sales_count == 42

    def test_another_accounts_order_does_not_exist(
        self, basket: Any, bob: Any, address: Address, shipping: ShippingMethod
    ) -> None:
        order = _checkout(basket, address, shipping)

        with pytest.raises(ShopNotFound):
            shop_service.confirm_payment(bob, order.number)


class TestCancellation:
    def test_cancelling_puts_the_stock_back(
        self, basket: Any, laptop: Product, address: Address, shipping: ShippingMethod
    ) -> None:
        order = _checkout(basket, address, shipping)

        shop_service.cancel_order(basket, order.number)
        laptop.refresh_from_db()

        assert laptop.stock == 5
        assert order.reservations.get().released_at is not None

    def test_cancelling_twice_releases_the_stock_once(
        self, basket: Any, laptop: Product, address: Address, shipping: ShippingMethod
    ) -> None:
        order = _checkout(basket, address, shipping)
        shop_service.cancel_order(basket, order.number)

        with pytest.raises(ShopRefused):
            shop_service.cancel_order(basket, order.number)

        laptop.refresh_from_db()
        assert laptop.stock == 5

    def test_a_paid_order_cannot_be_cancelled_here(
        self, basket: Any, address: Address, shipping: ShippingMethod
    ) -> None:
        """Refunding is a different operation with different consequences."""
        order = _checkout(basket, address, shipping)
        shop_service.confirm_payment(basket, order.number)

        with pytest.raises(ShopRefused):
            shop_service.cancel_order(basket, order.number)

    def test_a_cancelled_order_can_no_longer_be_paid(
        self, basket: Any, address: Address, shipping: ShippingMethod
    ) -> None:
        order = _checkout(basket, address, shipping)
        shop_service.cancel_order(basket, order.number)

        assert shop_service.confirm_payment(basket, order.number).status == OrderStatus.CANCELLED

    def test_an_untracked_product_has_no_stock_to_put_back(
        self, alice: Any, laptop: Product, address: Address, shipping: ShippingMethod
    ) -> None:
        laptop.track_inventory = False
        laptop.stock = 0
        laptop.save()
        shop_service.add_to_cart(alice, "featherbook-14")
        order = _checkout(alice, address, shipping)

        shop_service.cancel_order(alice, order.number)
        laptop.refresh_from_db()

        assert laptop.stock == 0
        assert InventoryReservation.objects.get().released_at is not None


class TestCheckoutOverHttp:
    def test_it_needs_a_credential(self, client: Client, db: None) -> None:
        assert client.post(f"{SHOP}/checkout").status_code == 401

    def test_the_whole_purchase_works_over_http(
        self, client: Client, basket: Any, address: Address, shipping: ShippingMethod
    ) -> None:
        placed = client.post(
            f"{SHOP}/checkout",
            data={"address": str(address.pk), "shipping_method": str(shipping.pk)},
            content_type="application/json",
            **bearer(basket),
        )
        assert placed.status_code == 200, placed.content
        order = placed.json()["data"]

        paid = client.post(
            f"{SHOP}/orders/{order['number']}/payment/confirm",
            data={"reference": "psp_2"},
            content_type="application/json",
            **bearer(basket),
        )

        assert paid.status_code == 200, paid.content
        assert paid.json()["data"]["status"] == "paid"

    def test_an_order_can_be_cancelled_over_http(
        self, client: Client, basket: Any, address: Address, shipping: ShippingMethod
    ) -> None:
        order = _checkout(basket, address, shipping)

        response = client.post(f"{SHOP}/orders/{order.number}/cancel", **bearer(basket))

        assert response.status_code == 200, response.content
        assert response.json()["data"]["status"] == "cancelled"

    def test_an_empty_basket_is_a_400(
        self, client: Client, alice: Any, address: Address, shipping: ShippingMethod
    ) -> None:
        response = client.post(
            f"{SHOP}/checkout",
            data={"address": str(address.pk), "shipping_method": str(shipping.pk)},
            content_type="application/json",
            **bearer(alice),
        )

        assert response.status_code == 400

    def test_the_currency_is_the_shops(
        self, basket: Any, address: Address, shipping: ShippingMethod
    ) -> None:
        with override_settings(SHOP_CURRENCY="eur"):
            order = _checkout(basket, address, shipping)

        assert order.currency == "EUR"
