"""What happens to an order after the money arrives.

Checkout is covered next door; this is the half that used to be unreachable.
Four of the seven statuses an order can hold had no path to them at all, so the
things worth being strict about here are that the path exists, that it is
one-way, and that the two steps which move stock -- cancelling and refunding --
put back exactly what was taken and put it back once.
"""

from decimal import Decimal
from typing import Any

import pytest

from apps.shop.models import (
    Address,
    InventoryReservation,
    Order,
    OrderStatus,
    Payment,
    PaymentStatus,
    Product,
    ShippingMethod,
)
from apps.shop.services import ShopRefused, shop_service

pytestmark = pytest.mark.django_db


@pytest.fixture
def paid(alice: Any, laptop: Product, address: Address, shipping: ShippingMethod) -> Order:
    """Two laptops bought and paid for: the state every step below starts from."""
    shop_service.add_to_cart(alice, "featherbook-14", quantity=2)
    order = shop_service.checkout(alice, address_id=address.pk, shipping_method_id=shipping.pk)
    return shop_service.confirm_payment(alice, order.number)


def _stock(product: Product) -> int:
    product.refresh_from_db()
    return product.stock


class TestTheHappyPath:
    def test_a_paid_order_walks_all_the_way_to_completed(self, paid: Order) -> None:
        shop_service.start_processing(paid)
        shop_service.ship_order(paid, carrier="Royal Mail", tracking_number="RM123")
        order = shop_service.complete_order(paid)

        assert order.status == OrderStatus.COMPLETED

    def test_dispatching_stamps_the_tracking_a_shopper_is_waiting_for(self, paid: Order) -> None:
        shop_service.start_processing(paid)

        order = shop_service.ship_order(
            paid,
            carrier="Royal Mail",
            tracking_number="RM123",
            tracking_url="https://tracking.example.com/RM123",
        )

        assert order.carrier == "Royal Mail"
        assert order.tracking_number == "RM123"
        assert order.tracking_url == "https://tracking.example.com/RM123"
        assert order.shipped_at is not None

    def test_every_step_leaves_a_line_in_the_history(self, paid: Order) -> None:
        shop_service.start_processing(paid, note="Picking.")
        shop_service.ship_order(paid, carrier="Royal Mail")

        assert [str(event.status) for event in paid.events.order_by("created_at")] == [
            OrderStatus.PENDING,
            OrderStatus.PAID,
            OrderStatus.PROCESSING,
            OrderStatus.SHIPPED,
        ]

    def test_the_history_records_who_took_the_step(self, paid: Order, bob: Any) -> None:
        shop_service.start_processing(paid, actor=bob)

        assert paid.events.order_by("-created_at").first().actor == bob

    def test_asking_for_the_status_it_already_has_changes_nothing(self, paid: Order) -> None:
        before = paid.events.count()

        assert shop_service.advance_order(paid, str(OrderStatus.PAID)).status == OrderStatus.PAID
        assert paid.events.count() == before


class TestTheMapIsOneWay:
    def test_an_order_cannot_skip_the_middle(self, paid: Order) -> None:
        with pytest.raises(ShopRefused):
            shop_service.complete_order(paid)

    def test_a_refunded_order_is_not_walked_back(self, paid: Order) -> None:
        shop_service.refund_order(paid)

        with pytest.raises(ShopRefused):
            shop_service.start_processing(paid)

    def test_a_shipped_order_cannot_be_cancelled(self, alice: Any, paid: Order) -> None:
        shop_service.start_processing(paid)
        shop_service.ship_order(paid)

        with pytest.raises(ShopRefused):
            shop_service.cancel_order(alice, paid.number)

    def test_a_paid_order_is_refunded_rather_than_cancelled(self, alice: Any, paid: Order) -> None:
        """Cancelling would release the stock while the money stayed taken."""
        with pytest.raises(ShopRefused):
            shop_service.cancel_order(alice, paid.number)

    def test_the_refusal_names_the_steps_that_would_have_worked(self, paid: Order) -> None:
        shop_service.start_processing(paid)
        shop_service.ship_order(paid)
        shop_service.complete_order(paid)

        with pytest.raises(ShopRefused, match="Refunded"):
            shop_service.start_processing(paid)


class TestRefundingMovesStockAndMoney:
    def test_refunding_puts_the_stock_back(self, paid: Order, laptop: Product) -> None:
        sold = _stock(laptop)

        shop_service.refund_order(paid)

        assert _stock(laptop) == sold + 2

    def test_refunding_twice_puts_it_back_once(self, paid: Order, laptop: Product) -> None:
        """Asking again is not an error -- it is the same answer, and no second restock."""
        shop_service.refund_order(paid)
        after = _stock(laptop)

        shop_service.refund_order(paid)

        assert _stock(laptop) == after

    def test_every_reservation_is_closed(self, paid: Order) -> None:
        shop_service.refund_order(paid)

        assert not InventoryReservation.objects.filter(order=paid, released_at=None).exists()

    def test_the_sale_no_longer_counts(self, paid: Order, laptop: Product) -> None:
        counted = Product.objects.get(pk=laptop.pk).sales_count

        shop_service.refund_order(paid)

        assert Product.objects.get(pk=laptop.pk).sales_count == counted - 2

    def test_the_money_is_marked_returned(self, paid: Order) -> None:
        shop_service.refund_order(paid, reason="Arrived broken.")

        assert list(paid.payments.values_list("status", flat=True)) == [PaymentStatus.REFUNDED]

    def test_the_reason_is_kept_on_the_history(self, paid: Order) -> None:
        shop_service.refund_order(paid, reason="Arrived broken.")

        assert paid.events.order_by("-created_at").first().note == "Arrived broken."

    def test_an_order_refunded_after_dispatch_still_restocks(
        self, paid: Order, laptop: Product
    ) -> None:
        sold = _stock(laptop)
        shop_service.start_processing(paid)
        shop_service.ship_order(paid)

        shop_service.refund_order(paid)

        assert _stock(laptop) == sold + 2


class TestWhatTheShopperSees:
    def test_the_order_payload_carries_its_tracking_and_history(
        self, alice: Any, paid: Order
    ) -> None:
        shop_service.start_processing(paid)
        shop_service.ship_order(paid, carrier="Royal Mail", tracking_number="RM123")

        payload = shop_service.order(alice, paid.number)

        assert payload["carrier"] == "Royal Mail"
        assert payload["tracking_number"] == "RM123"
        assert [step["status"] for step in payload["history"]] == [
            OrderStatus.PENDING,
            OrderStatus.PAID,
            OrderStatus.PROCESSING,
            OrderStatus.SHIPPED,
        ]

    def test_an_open_order_is_open_until_it_is_finished(self, paid: Order) -> None:
        assert paid.is_open

        shop_service.start_processing(paid)
        shop_service.ship_order(paid)

        assert Order.objects.get(pk=paid.pk).is_open
        assert not shop_service.complete_order(paid).is_open

    def test_a_pending_order_is_still_the_shopper_s_to_cancel(
        self, alice: Any, laptop: Product, address: Address, shipping: ShippingMethod
    ) -> None:
        shop_service.add_to_cart(alice, "featherbook-14", quantity=1)
        order = shop_service.checkout(alice, address_id=address.pk, shipping_method_id=shipping.pk)

        assert shop_service.cancel_order(alice, order.number).status == OrderStatus.CANCELLED

    def test_the_amount_collected_is_read_off_the_payments(self, paid: Order) -> None:
        assert paid.paid_amount == Decimal(str(paid.total))
        assert Payment.objects.filter(order=paid, status=PaymentStatus.SUCCEEDED).count() == 1
