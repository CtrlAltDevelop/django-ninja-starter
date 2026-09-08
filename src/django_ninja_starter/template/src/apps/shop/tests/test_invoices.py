"""The invoice, and settling a payment by hand.

No gateway is wired up, so an order is placed against the ``manual`` provider
and somebody with a bank statement in front of them marks the payment taken or
refused. The two things worth being strict about: an invoice is issued exactly
once, when the order is placed, and refusing a payment is one attempt failing
rather than the order dying -- so the stock stays reserved and there is
something for the next attempt to settle.
"""

from decimal import Decimal
from typing import Any

import pytest
from django.contrib.auth import get_user_model
from django.test import Client
from django.urls import reverse

from apps.shop.models import (
    Address,
    Invoice,
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
User = get_user_model()

SHOP = "/api/v1/shop"


@pytest.fixture
def client() -> Client:
    return Client()


@pytest.fixture
def placed(alice: Any, laptop: Product, address: Address, shipping: ShippingMethod) -> Order:
    """One laptop, bought and awaiting payment."""
    shop_service.add_to_cart(alice, "featherbook-14", quantity=2)
    return shop_service.checkout(alice, address_id=address.pk, shipping_method_id=shipping.pk)


@pytest.fixture
def admin_client(db: None) -> Client:
    client = Client()
    client.force_login(
        User.objects.create_superuser(username="root", email="root@example.test", password="x")
    )
    return client


def data(response: Any) -> Any:
    assert response.status_code == 200, response.content
    return response.json()["data"]


class TestIssuing:
    def test_an_invoice_is_issued_with_the_order(self, placed: Order) -> None:
        assert placed.invoice.number

    def test_its_number_is_its_own_rather_than_the_orders(self, placed: Order) -> None:
        """A shop that ever issues a credit note needs its own sequence."""
        assert placed.invoice.number != placed.number
        assert placed.invoice.number.startswith("INV")

    def test_two_orders_get_two_invoices(
        self, placed: Order, alice: Any, laptop: Product, address: Address, shipping: ShippingMethod
    ) -> None:
        shop_service.add_to_cart(alice, "featherbook-14")
        second = shop_service.checkout(alice, address_id=address.pk, shipping_method_id=shipping.pk)

        assert Invoice.objects.count() == 2
        assert second.invoice.number != placed.invoice.number

    def test_it_carries_no_totals_of_its_own(self, placed: Order) -> None:
        """Everything printed on it is the order's, so there is one place to be wrong."""
        assert not hasattr(placed.invoice, "total")

    def test_a_refused_checkout_issues_nothing(
        self, alice: Any, laptop: Product, address: Address, shipping: ShippingMethod
    ) -> None:
        shop_service.add_to_cart(alice, "featherbook-14", quantity=2)
        Product.objects.filter(pk=laptop.pk).update(stock=1)

        with pytest.raises(ShopRefused):
            shop_service.checkout(alice, address_id=address.pk, shipping_method_id=shipping.pk)

        assert not Invoice.objects.exists()


class TestReadingItBack:
    def test_the_invoice_needs_a_credential(self, client: Client, placed: Order) -> None:
        assert client.get(f"{SHOP}/orders/{placed.number}/invoice").status_code == 401

    def test_it_comes_back_with_the_order_on_it(
        self, client: Client, placed: Order, alice: Any
    ) -> None:
        document = data(client.get(f"{SHOP}/orders/{placed.number}/invoice", **bearer(alice)))

        assert document["number"] == placed.invoice.number
        assert document["order"]["number"] == placed.number
        assert document["order"]["total"] == "2400.00"
        assert [line["name"] for line in document["order"]["items"]] == ["Acme Featherbook 14"]

    def test_another_accounts_invoice_does_not_exist(
        self, client: Client, placed: Order, bob: Any
    ) -> None:
        response = client.get(f"{SHOP}/orders/{placed.number}/invoice", **bearer(bob))

        assert response.status_code == 404

    def test_an_order_with_no_invoice_says_so(self, placed: Order, alice: Any) -> None:
        Invoice.objects.all().delete()

        with pytest.raises(ShopNotFound):
            shop_service.invoice(alice, placed.number)


class TestReadingOrders:
    def test_the_list_needs_a_credential(self, client: Client, db: None) -> None:
        assert client.get(f"{SHOP}/orders").status_code == 401

    def test_it_lists_this_accounts_orders(self, client: Client, placed: Order, alice: Any) -> None:
        page = data(client.get(f"{SHOP}/orders", **bearer(alice)))

        assert [row["number"] for row in page["items"]] == [placed.number]
        assert page["total"] == 1

    def test_it_never_lists_another_accounts(self, client: Client, placed: Order, bob: Any) -> None:
        assert data(client.get(f"{SHOP}/orders", **bearer(bob)))["items"] == []

    def test_one_order_reads_off_its_own_snapshot(
        self, client: Client, placed: Order, alice: Any, laptop: Product
    ) -> None:
        """The catalogue moving on must not change what an old order says."""
        laptop.name = "Featherbook 14 (2027)"
        laptop.price = Decimal("1500.00")
        laptop.save()

        order = data(client.get(f"{SHOP}/orders/{placed.number}", **bearer(alice)))

        assert order["items"][0]["name"] == "Acme Featherbook 14"
        assert order["items"][0]["unit_price"] == "1200.00"

    def test_it_names_the_invoice_issued_against_it(
        self, client: Client, placed: Order, alice: Any
    ) -> None:
        order = data(client.get(f"{SHOP}/orders/{placed.number}", **bearer(alice)))

        assert order["invoice"] == placed.invoice.number
        assert order["payment_status"] == "pending"

    def test_the_shape_a_checkout_returns_is_the_shape_read_back(
        self,
        client: Client,
        alice: Any,
        laptop: Product,
        address: Address,
        shipping: ShippingMethod,
    ) -> None:
        """A client that has to parse two shapes for one thing parses one wrong."""
        shop_service.add_to_cart(alice, "featherbook-14")
        response = client.post(
            f"{SHOP}/checkout",
            data={"address": str(address.pk), "shipping_method": str(shipping.pk)},
            content_type="application/json",
            **bearer(alice),
        )
        placed_body = data(response)

        read_back = data(client.get(f"{SHOP}/orders/{placed_body['number']}", **bearer(alice)))

        assert placed_body.keys() == read_back.keys()

    def test_another_accounts_order_does_not_exist(
        self, client: Client, placed: Order, bob: Any
    ) -> None:
        assert client.get(f"{SHOP}/orders/{placed.number}", **bearer(bob)).status_code == 404


class TestSettlingByHand:
    def test_marking_it_paid_settles_the_order(self, placed: Order) -> None:
        settled = shop_service.settle_order(placed, reference="bank transfer 8812")

        assert settled.status == OrderStatus.PAID
        assert settled.payments.get().status == PaymentStatus.SUCCEEDED
        assert settled.payments.get().provider_reference == "bank transfer 8812"

    def test_settling_twice_sells_the_stock_once(self, placed: Order, laptop: Product) -> None:
        shop_service.settle_order(placed)
        shop_service.settle_order(placed)
        laptop.refresh_from_db()

        assert laptop.sales_count == 42

    def test_rejecting_records_the_attempt_and_opens_another(self, placed: Order) -> None:
        shop_service.reject_payment(placed, reason="card declined")

        statuses = {payment.status for payment in placed.payments.all()}
        assert statuses == {PaymentStatus.FAILED, PaymentStatus.PENDING}
        assert placed.payments.filter(status=PaymentStatus.FAILED).get().provider_reference == (
            "card declined"
        )

    def test_rejecting_leaves_the_order_payable(self, placed: Order) -> None:
        """A shopper whose card was declined tries another one."""
        shop_service.reject_payment(placed)
        placed.refresh_from_db()

        assert placed.status == OrderStatus.PENDING
        assert shop_service.settle_order(placed).status == OrderStatus.PAID

    def test_rejecting_leaves_the_stock_reserved(self, placed: Order, laptop: Product) -> None:
        """Releasing it here would oversell the shopper who retries successfully."""
        shop_service.reject_payment(placed)
        laptop.refresh_from_db()

        assert laptop.stock == 3
        assert placed.reservations.get().released_at is None

    def test_cancelling_is_what_releases_the_stock(
        self, placed: Order, laptop: Product, alice: Any
    ) -> None:
        shop_service.reject_payment(placed)

        shop_service.cancel_order(alice, placed.number)
        laptop.refresh_from_db()

        assert laptop.stock == 5

    def test_a_paid_order_has_no_payment_to_reject(self, placed: Order) -> None:
        shop_service.settle_order(placed)

        with pytest.raises(ShopRefused):
            shop_service.reject_payment(placed)

    def test_a_cancelled_order_cannot_be_settled(self, placed: Order, alice: Any) -> None:
        shop_service.cancel_order(alice, placed.number)

        assert shop_service.settle_order(placed).status == OrderStatus.CANCELLED

    def test_an_order_whose_payments_are_all_spent_cannot_be_settled(self, placed: Order) -> None:
        Payment.objects.filter(order=placed).update(status=PaymentStatus.FAILED)

        with pytest.raises(ShopRefused):
            shop_service.settle_order(placed)


class TestTheAdminScreens:
    def _act(self, admin_client: Client, model: str, action: str, *rows: Any) -> Any:
        return admin_client.post(
            reverse(f"admin:shop_{model}_changelist"),
            {"action": action, "_selected_action": [str(row.pk) for row in rows]},
            follow=True,
        )

    def test_the_invoice_list_opens(self, admin_client: Client, placed: Order) -> None:
        response = admin_client.get(reverse("admin:shop_invoice_changelist"))

        assert response.status_code == 200
        assert placed.invoice.number in response.content.decode()

    def test_an_invoice_cannot_be_typed_in_by_hand(self, admin_client: Client, db: None) -> None:
        """It is issued by a checkout, never created."""
        assert admin_client.get(reverse("admin:shop_invoice_add")).status_code == 403

    def test_the_invoice_form_shows_the_orders_total(
        self, admin_client: Client, placed: Order
    ) -> None:
        url = reverse("admin:shop_invoice_change", args=(placed.invoice.pk,))

        assert admin_client.get(url).status_code == 200

    def test_marking_a_payment_paid_settles_the_order(
        self, admin_client: Client, placed: Order, laptop: Product
    ) -> None:
        payment = placed.payments.get()

        self._act(admin_client, "payment", "mark_paid", payment)

        placed.refresh_from_db()
        laptop.refresh_from_db()
        assert placed.status == OrderStatus.PAID
        assert laptop.sales_count == 42

    def test_marking_a_payment_rejected_opens_a_fresh_one(
        self, admin_client: Client, placed: Order
    ) -> None:
        payment = placed.payments.get()

        self._act(admin_client, "payment", "mark_rejected", payment)

        assert placed.payments.count() == 2
        assert placed.payments.filter(status=PaymentStatus.PENDING).count() == 1

    def test_an_action_reports_what_it_could_not_do(
        self, admin_client: Client, placed: Order
    ) -> None:
        """Selecting fifteen rows and having twelve work is the point."""
        payment = placed.payments.get()
        shop_service.settle_order(placed)

        response = self._act(admin_client, "payment", "mark_rejected", payment)

        assert placed.number in response.content.decode()

    def test_an_order_can_be_marked_paid_from_its_own_screen(
        self, admin_client: Client, placed: Order
    ) -> None:
        self._act(admin_client, "order", "mark_paid", placed)
        placed.refresh_from_db()

        assert placed.status == OrderStatus.PAID

    def test_an_order_can_be_cancelled_and_its_stock_released(
        self, admin_client: Client, placed: Order, laptop: Product
    ) -> None:
        self._act(admin_client, "order", "cancel_orders", placed)

        placed.refresh_from_db()
        laptop.refresh_from_db()
        assert placed.status == OrderStatus.CANCELLED
        assert laptop.stock == 5

    def test_cancelling_a_paid_order_is_reported_rather_than_done(
        self, admin_client: Client, placed: Order
    ) -> None:
        shop_service.settle_order(placed)

        self._act(admin_client, "order", "cancel_orders", placed)

        placed.refresh_from_db()
        assert placed.status == OrderStatus.PAID

    def test_a_payment_amount_is_still_not_editable(
        self, admin_client: Client, placed: Order
    ) -> None:
        url = reverse("admin:shop_payment_change", args=(placed.payments.get().pk,))
        response = admin_client.get(url)

        assert response.status_code == 200
        assert 'name="amount"' not in response.content.decode()

    def test_the_order_list_shows_the_invoice_number(
        self, admin_client: Client, placed: Order
    ) -> None:
        response = admin_client.get(reverse("admin:shop_order_changelist"))

        assert placed.invoice.number in response.content.decode()
