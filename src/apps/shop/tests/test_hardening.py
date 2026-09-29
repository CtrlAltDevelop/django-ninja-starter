"""Regressions for audit findings: each test fails on the code as it was."""

from typing import Any

import pytest
from django.contrib.auth import get_user_model
from django.test import Client
from django.urls import reverse

from apps.shop import signals
from apps.shop.models import (
    Address,
    Coupon,
    Order,
    OrderEvent,
    PaymentStatus,
    Product,
    ProductVariant,
    Review,
    ReviewStatus,
    ShippingMethod,
)
from apps.shop.payloads import review_payload
from apps.shop.services import ShopNotFound, shop_service

pytestmark = pytest.mark.django_db
User = get_user_model()


@pytest.fixture
def admin_client() -> Client:
    client = Client()
    client.force_login(
        User.objects.create_superuser(username="root", email="root@example.test", password="x")
    )
    return client


class TestReviewAuthor:
    def test_a_phone_account_is_not_published_by_its_username(self, laptop: Product) -> None:
        user = User.objects.create_user(username="user447700900123.ab12cd34")
        review = Review.objects.create(product=laptop, user=user, rating=5, body="Good.")

        assert review_payload(review)["author"] == "Verified buyer"

    def test_a_named_account_is_first_name_and_last_initial(self, laptop: Product) -> None:
        from infrastructure.accounts.models import Profile

        user = User.objects.create_user(username="x.1")
        Profile.objects.update_or_create(user=user, defaults={"display_name": "Alice Smith"})
        review = Review.objects.create(product=laptop, user=user, rating=5, body="Good.")

        fresh = Review.objects.select_related("user__profile").get(pk=review.pk)
        assert review_payload(fresh)["author"] == "Alice S."


class TestCheckout:
    def test_lines_added_in_either_order_are_all_bought(
        self,
        alice: Any,
        laptop: Product,
        medium: ProductVariant,
        address: Address,
        shipping: ShippingMethod,
    ) -> None:
        """Rows are locked up front in pk order, and every line still finds its own."""
        shop_service.add_to_cart(alice, "plain-tee", variant_id=medium.pk)
        shop_service.add_to_cart(alice, "featherbook-14")

        order = shop_service.checkout(alice, address_id=address.pk, shipping_method_id=shipping.pk)

        assert sorted(order.items.values_list("sku", flat=True)) == ["FB-14", "TEE-M"]

    def test_a_malformed_address_or_shipping_id_is_not_found(
        self, alice: Any, laptop: Product
    ) -> None:
        shop_service.add_to_cart(alice, "featherbook-14")

        with pytest.raises(ShopNotFound):
            shop_service.checkout(alice, address_id="nope", shipping_method_id="nope")

    def test_the_shopper_cannot_choose_the_provider(
        self, alice: Any, laptop: Product, address: Address, shipping: ShippingMethod
    ) -> None:
        from apps.shop.tests.conftest import bearer

        shop_service.add_to_cart(alice, "featherbook-14")
        response = Client().post(
            "/api/v1/shop/checkout",
            {
                "address": str(address.pk),
                "shipping_method": str(shipping.pk),
                "provider": "stripe",
            },
            content_type="application/json",
            **bearer(alice),
        )

        assert response.status_code < 300, response.content
        assert Order.objects.get(user=alice).payments.get().provider == "manual"

    def test_a_coupon_with_a_trailing_space_is_used(
        self,
        alice: Any,
        laptop: Product,
        address: Address,
        shipping: ShippingMethod,
        coupon: Coupon,
    ) -> None:
        shop_service.add_to_cart(alice, "featherbook-14")

        order = shop_service.checkout(
            alice, address_id=address.pk, shipping_method_id=shipping.pk, coupon_code="WELCOME "
        )

        assert order.coupon == coupon


class TestAdmin:
    def test_approving_on_the_change_form_announces_it(
        self,
        admin_client: Client,
        laptop: Product,
        alice: Any,
        django_capture_on_commit_callbacks: Any,
    ) -> None:
        review = Review.objects.create(
            product=laptop, user=alice, rating=5, body="Great.", status=ReviewStatus.PENDING
        )
        heard: list[Any] = []

        def listener(**kwargs: Any) -> None:
            heard.append(kwargs["review"]["id"])

        signals.review_published.connect(listener)
        try:
            with django_capture_on_commit_callbacks(execute=True):
                admin_client.post(
                    reverse("admin:shop_review_change", args=(review.pk,)),
                    {"status": str(ReviewStatus.APPROVED), "moderator_note": ""},
                )
        finally:
            signals.review_published.disconnect(listener)

        review.refresh_from_db()
        assert review.status == ReviewStatus.APPROVED
        assert heard == [review.pk]

    def test_rejecting_an_old_attempt_leaves_the_pending_one_alone(
        self,
        admin_client: Client,
        laptop: Product,
        alice: Any,
        address: Address,
        shipping: ShippingMethod,
    ) -> None:
        shop_service.add_to_cart(alice, "featherbook-14")
        order = shop_service.checkout(alice, address_id=address.pk, shipping_method_id=shipping.pk)
        shop_service.reject_payment(order)
        failed = order.payments.get(status=PaymentStatus.FAILED)

        admin_client.post(
            reverse("admin:shop_payment_changelist"),
            {"action": "mark_rejected", "_selected_action": [str(failed.pk)]},
        )

        assert order.payments.filter(status=PaymentStatus.PENDING).count() == 1
        assert order.payments.count() == 2

    def test_marking_paid_records_who_did_it(
        self,
        admin_client: Client,
        laptop: Product,
        alice: Any,
        address: Address,
        shipping: ShippingMethod,
    ) -> None:
        shop_service.add_to_cart(alice, "featherbook-14")
        order = shop_service.checkout(alice, address_id=address.pk, shipping_method_id=shipping.pk)

        admin_client.post(
            reverse("admin:shop_payment_changelist"),
            {"action": "mark_paid", "_selected_action": [str(order.payments.get().pk)]},
        )

        event = OrderEvent.objects.filter(order=order).latest("created_at")
        assert event.actor is not None
        assert event.actor.get_username() == "root"

    def test_rejecting_records_who_did_it(
        self,
        admin_client: Client,
        laptop: Product,
        alice: Any,
        address: Address,
        shipping: ShippingMethod,
    ) -> None:
        shop_service.add_to_cart(alice, "featherbook-14")
        order = shop_service.checkout(alice, address_id=address.pk, shipping_method_id=shipping.pk)

        admin_client.post(
            reverse("admin:shop_payment_changelist"),
            {"action": "mark_rejected", "_selected_action": [str(order.payments.get().pk)]},
        )

        event = OrderEvent.objects.filter(order=order).latest("created_at")
        assert event.note.startswith("Payment attempt refused")
        assert event.actor is not None
        assert event.actor.get_username() == "root"
