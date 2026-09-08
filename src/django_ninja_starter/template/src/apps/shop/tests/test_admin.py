"""The admin, which is where a catalogue is actually built.

Nothing in this app creates a product over the API, on purpose, so these screens
are the whole write path and the tests for them are not optional. Every
changelist and every change form is opened for real, because a broken
``list_display`` or a column reading a relation that is not loaded is a 500 that
only a request finds.
"""

from decimal import Decimal
from typing import Any

import pytest
from django.contrib.auth import get_user_model
from django.test import Client
from django.urls import reverse

from apps.shop.models import (
    Address,
    Brand,
    Cart,
    Category,
    Collection,
    Coupon,
    Discount,
    OrderStatus,
    PaymentStatus,
    Product,
    ProductLike,
    ProductStatus,
    Review,
    ReviewStatus,
    ShippingMethod,
)
from apps.shop.services import shop_service

pytestmark = pytest.mark.django_db
User = get_user_model()

#: Every model this app registers, so a new one cannot be added without its
#: changelist being opened at least once.
REGISTERED = [
    "brand",
    "tag",
    "category",
    "categoryattribute",
    "product",
    "productvariant",
    "collection",
    "discount",
    "cart",
    "review",
    "productlike",
    "shippingmethod",
    "coupon",
    "order",
    "payment",
    "seller",
    "productoffer",
    "invoice",
    "address",
    "inventoryreservation",
    "orderevent",
]


@pytest.fixture
def superuser() -> Any:
    return User.objects.create_superuser(username="root", email="root@example.test", password="x")


@pytest.fixture
def admin_client(superuser: Any) -> Client:
    client = Client()
    client.force_login(superuser)
    return client


def changelist(model: str) -> str:
    return reverse(f"admin:shop_{model}_changelist")


class TestEveryScreenOpens:
    @pytest.mark.parametrize("model", REGISTERED)
    def test_a_changelist_opens(self, admin_client: Client, model: str) -> None:
        assert admin_client.get(changelist(model)).status_code == 200

    @pytest.mark.parametrize("model", REGISTERED)
    def test_a_changelist_opens_with_rows_on_it(
        self,
        admin_client: Client,
        model: str,
        laptop: Product,
        tshirt: Product,
        sale: Discount,
        staff_picks: Collection,
        alice: Any,
        address: Address,
        shipping: ShippingMethod,
        coupon: Coupon,
    ) -> None:
        """A column that reads a relation is a query, and a missing one is a 500."""
        Review.objects.create(
            product=laptop, user=alice, rating=4, body="Good.", status=ReviewStatus.APPROVED
        )
        ProductLike.objects.create(product=laptop, user=alice)
        shop_service.add_to_cart(alice, "featherbook-14")
        shop_service.checkout(alice, address_id=address.pk, shipping_method_id=shipping.pk)

        assert admin_client.get(changelist(model)).status_code == 200

    def test_the_product_form_opens(self, admin_client: Client, laptop: Product) -> None:
        url = reverse("admin:shop_product_change", args=(laptop.pk,))

        assert admin_client.get(url).status_code == 200

    def test_the_add_product_form_opens(self, admin_client: Client, laptops: Category) -> None:
        """The read-only price and completeness rows have no product to read yet."""
        assert admin_client.get(reverse("admin:shop_product_add")).status_code == 200

    def test_the_category_form_opens_with_its_attribute_rows(
        self, admin_client: Client, laptops: Category
    ) -> None:
        url = reverse("admin:shop_category_change", args=(laptops.pk,))

        assert admin_client.get(url).status_code == 200

    def test_an_order_form_opens(
        self,
        admin_client: Client,
        laptop: Product,
        alice: Any,
        address: Address,
        shipping: ShippingMethod,
    ) -> None:
        shop_service.add_to_cart(alice, "featherbook-14")
        order = shop_service.checkout(alice, address_id=address.pk, shipping_method_id=shipping.pk)

        url = reverse("admin:shop_order_change", args=(order.pk,))

        assert admin_client.get(url).status_code == 200

    def test_a_basket_opens_as_a_support_screen(
        self, admin_client: Client, laptop: Product, alice: Any
    ) -> None:
        shop_service.add_to_cart(alice, "featherbook-14", quantity=2)
        cart = Cart.objects.get(user=alice)

        response = admin_client.get(reverse("admin:shop_cart_change", args=(cart.pk,)))

        assert response.status_code == 200


class TestSignedOut:
    def test_the_catalogue_is_not_open_to_the_public(self, client: Client, db: None) -> None:
        response = Client().get(changelist("product"))

        assert response.status_code == 302
        assert "/login" in response["Location"]


class TestProductColumns:
    def test_the_price_column_shows_the_campaign_making_it(
        self, admin_client: Client, laptop: Product, sale: Discount
    ) -> None:
        page = admin_client.get(changelist("product")).content.decode()

        assert "1080.00" in page
        assert "Spring sale" in page

    def test_the_price_column_is_the_plain_price_with_nothing_running(
        self, admin_client: Client, laptop: Product
    ) -> None:
        page = admin_client.get(changelist("product")).content.decode()

        assert "1200.00" in page

    def test_a_scheduled_product_says_so_rather_than_reading_as_live(
        self, admin_client: Client, laptop: Product
    ) -> None:
        from django.utils import timezone

        laptop.published_at = timezone.now() + timezone.timedelta(days=3)
        laptop.save()

        page = admin_client.get(changelist("product")).content.decode()

        assert "Scheduled for" in page

    def test_a_product_missing_a_required_attribute_is_flagged_on_its_form(
        self, admin_client: Client, tshirt: Product, shirts: Category
    ) -> None:
        from apps.shop.models import CategoryAttribute

        CategoryAttribute.objects.create(
            category=shirts, name="Fabric", code="fabric", required=True
        )
        url = reverse("admin:shop_product_change", args=(tshirt.pk,))

        assert "Fabric" in admin_client.get(url).content.decode()

    def test_a_complete_product_says_nothing_is_outstanding(
        self, admin_client: Client, laptop: Product
    ) -> None:
        url = reverse("admin:shop_product_change", args=(laptop.pk,))

        assert "every required attribute is answered" in admin_client.get(url).content.decode()


class TestStockFilter:
    def _filtered(self, admin_client: Client, state: str) -> list[str]:
        response = admin_client.get(changelist("product"), {"stock_state": state})
        assert response.status_code == 200
        return [product.name for product in response.context["cl"].result_list]

    def test_out_of_stock_finds_the_empty_shelf(
        self, admin_client: Client, laptop: Product
    ) -> None:
        laptop.stock = 0
        laptop.save()

        assert self._filtered(admin_client, "out") == ["Acme Featherbook 14"]

    def test_running_low_finds_what_is_nearly_gone(
        self, admin_client: Client, laptop: Product
    ) -> None:
        assert self._filtered(admin_client, "low") == ["Acme Featherbook 14"]

    def test_in_stock_finds_what_can_be_bought(
        self, admin_client: Client, laptop: Product, tshirt: Product
    ) -> None:
        assert set(self._filtered(admin_client, "in")) == {"Acme Featherbook 14", "Plain tee"}

    def test_no_choice_leaves_the_list_alone(
        self, admin_client: Client, laptop: Product, draft: Product
    ) -> None:
        assert len(self._filtered(admin_client, "")) == 2


class TestProductActions:
    def _act(self, admin_client: Client, action: str, *products: Product) -> Any:
        return admin_client.post(
            changelist("product"),
            {
                "action": action,
                "_selected_action": [str(product.pk) for product in products],
            },
            follow=True,
        )

    def test_publishing_makes_a_draft_live(self, admin_client: Client, draft: Product) -> None:
        self._act(admin_client, "publish", draft)
        draft.refresh_from_db()

        assert draft.status == ProductStatus.ACTIVE
        assert draft.is_live

    def test_publishing_clears_a_date_that_has_not_arrived(
        self, admin_client: Client, laptop: Product
    ) -> None:
        from django.utils import timezone

        laptop.published_at = timezone.now() + timezone.timedelta(days=3)
        laptop.save()

        self._act(admin_client, "publish", laptop)
        laptop.refresh_from_db()

        assert laptop.published_at is None

    def test_returning_to_draft_hides_it_from_the_api(
        self, admin_client: Client, laptop: Product
    ) -> None:
        self._act(admin_client, "unpublish", laptop)

        assert not Product.objects.live().exists()

    def test_archiving_keeps_the_row(self, admin_client: Client, laptop: Product) -> None:
        """Reviews and cart lines point at it, so it is archived rather than deleted."""
        self._act(admin_client, "archive", laptop)
        laptop.refresh_from_db()

        assert laptop.status == ProductStatus.ARCHIVED
        assert Product.objects.filter(pk=laptop.pk).exists()

    def test_featuring_puts_it_in_the_featured_listing(
        self, admin_client: Client, tshirt: Product
    ) -> None:
        self._act(admin_client, "feature", tshirt)

        listing = shop_service.listing("featured")

        assert [row["id"] for row in listing["items"]] == ["plain-tee"]

    def test_unfeaturing_takes_it_out_again(self, admin_client: Client, laptop: Product) -> None:
        self._act(admin_client, "unfeature", laptop)

        assert shop_service.listing("featured")["items"] == []

    def test_an_action_says_how_many_it_changed(
        self, admin_client: Client, laptop: Product, tshirt: Product
    ) -> None:
        response = self._act(admin_client, "feature", laptop, tshirt)

        assert "2 featured." in response.content.decode()


class TestReviewModeration:
    def _act(self, admin_client: Client, action: str, *reviews: Review) -> Any:
        return admin_client.post(
            changelist("review"),
            {
                "action": action,
                "_selected_action": [str(review.pk) for review in reviews],
            },
            follow=True,
        )

    @pytest.fixture
    def pending(self, laptop: Product, alice: Any) -> Review:
        return Review.objects.create(
            product=laptop, user=alice, rating=5, body="Great.", status=ReviewStatus.PENDING
        )

    def test_approving_publishes_it(self, admin_client: Client, pending: Review) -> None:
        self._act(admin_client, "approve", pending)
        pending.refresh_from_db()

        assert pending.status == ReviewStatus.APPROVED

    def test_approving_moves_the_products_rating(
        self, admin_client: Client, pending: Review, laptop: Product
    ) -> None:
        """One recomputation per product touched, not one per review."""
        self._act(admin_client, "approve", pending)
        laptop.refresh_from_db()

        assert laptop.rating_average == Decimal("5.00")
        assert laptop.rating_count == 1

    def test_rejecting_takes_it_back_out_of_the_rating(
        self, admin_client: Client, pending: Review, laptop: Product
    ) -> None:
        self._act(admin_client, "approve", pending)
        self._act(admin_client, "reject", pending)
        laptop.refresh_from_db()

        assert laptop.rating_count == 0

    def test_approving_two_reviews_of_one_product_recomputes_once(
        self, admin_client: Client, laptop: Product, alice: Any, bob: Any
    ) -> None:
        first = Review.objects.create(
            product=laptop, user=alice, rating=5, body="Great.", status=ReviewStatus.PENDING
        )
        second = Review.objects.create(
            product=laptop, user=bob, rating=3, body="Fine.", status=ReviewStatus.PENDING
        )

        self._act(admin_client, "approve", first, second)
        laptop.refresh_from_db()

        assert laptop.rating_count == 2
        assert laptop.rating_average == Decimal("4.00")

    def test_a_moderator_cannot_rewrite_what_a_shopper_said(
        self, admin_client: Client, pending: Review
    ) -> None:
        """A shop that can edit its reviews does not have reviews."""
        url = reverse("admin:shop_review_change", args=(pending.pk,))
        response = admin_client.get(url)

        assert response.status_code == 200
        assert 'name="body"' not in response.content.decode()


class TestRecordsAreReadOnly:
    def test_a_basket_cannot_be_added_by_hand(self, admin_client: Client, db: None) -> None:
        assert admin_client.get(reverse("admin:shop_cart_add")).status_code == 403

    def test_a_like_cannot_be_added_by_hand(self, admin_client: Client, db: None) -> None:
        assert admin_client.get(reverse("admin:shop_productlike_add")).status_code == 403

    def test_a_payment_cannot_be_edited(
        self,
        admin_client: Client,
        laptop: Product,
        alice: Any,
        address: Address,
        shipping: ShippingMethod,
    ) -> None:
        shop_service.add_to_cart(alice, "featherbook-14")
        order = shop_service.checkout(alice, address_id=address.pk, shipping_method_id=shipping.pk)
        payment = order.payments.get()

        url = reverse("admin:shop_payment_change", args=(payment.pk,))
        response = admin_client.get(url)

        assert response.status_code == 200
        assert 'name="amount"' not in response.content.decode()

    def test_a_record_can_still_be_deleted(
        self, admin_client: Client, laptop: Product, alice: Any
    ) -> None:
        """Removing somebody's data on request has to be possible."""
        like = ProductLike.objects.create(product=laptop, user=alice)

        url = reverse("admin:shop_productlike_delete", args=(like.pk,))

        assert admin_client.get(url).status_code == 200


class TestSearching:
    def test_a_product_is_found_by_its_sku(self, admin_client: Client, laptop: Product) -> None:
        response = admin_client.get(changelist("product"), {"q": "FB-14"})

        assert [row.name for row in response.context["cl"].result_list] == ["Acme Featherbook 14"]

    def test_an_order_is_found_by_its_number(
        self,
        admin_client: Client,
        laptop: Product,
        alice: Any,
        address: Address,
        shipping: ShippingMethod,
    ) -> None:
        shop_service.add_to_cart(alice, "featherbook-14")
        order = shop_service.checkout(alice, address_id=address.pk, shipping_method_id=shipping.pk)

        response = admin_client.get(changelist("order"), {"q": order.number})

        assert [row.number for row in response.context["cl"].result_list] == [order.number]

    def test_a_brand_lists_how_much_it_makes(
        self, admin_client: Client, laptop: Product, acme: Brand
    ) -> None:
        response = admin_client.get(changelist("brand"))

        assert response.status_code == 200
        assert "Acme" in response.content.decode()


class TestWritingThroughTheAdmin:
    def test_a_category_can_be_created(self, admin_client: Client, db: None) -> None:
        response = admin_client.post(
            reverse("admin:shop_category_add"),
            {
                "name": "Kitchen",
                "slug": "kitchen",
                "description": "",
                "image": "",
                "icon": "",
                "order": 0,
                "is_active": "on",
                "meta_title": "",
                "meta_description": "",
                "meta_keywords": "[]",
                "attributes-TOTAL_FORMS": "0",
                "attributes-INITIAL_FORMS": "0",
                "attributes-MIN_NUM_FORMS": "0",
                "attributes-MAX_NUM_FORMS": "1000",
            },
        )

        assert response.status_code == 302, response.content
        assert Category.objects.filter(slug="kitchen").exists()

    def test_a_discount_the_model_refuses_is_refused_here_too(
        self, admin_client: Client, db: None
    ) -> None:
        """The rules live in `clean`, so the form gets them for free."""
        response = admin_client.post(
            reverse("admin:shop_discount_add"),
            {
                "name": "Impossible",
                "description": "",
                "kind": "percent",
                "value": "150",
                "priority": 0,
                "is_active": "on",
            },
        )

        assert response.status_code == 200
        assert not Discount.objects.exists()


class TestNothingIsRegisteredWithoutAScreenTest:
    def test_the_list_above_is_the_whole_registry(self) -> None:
        """Otherwise a new model gets a screen nobody ever opens."""
        from django.contrib import admin as django_admin

        registered = {
            model._meta.model_name
            for model in django_admin.site._registry
            if model._meta.app_label == "shop"
        }

        assert registered == set(REGISTERED)


class TestTheOrderScreenMovesOrders:
    """The admin is the only place an order's status changes, so these are the
    write path for fulfilment the way the product form is for the catalogue."""

    @pytest.fixture
    def order(self, laptop: Product, alice: Any, address: Address, shipping: ShippingMethod) -> Any:
        shop_service.add_to_cart(alice, "featherbook-14", quantity=2)
        return shop_service.checkout(alice, address_id=address.pk, shipping_method_id=shipping.pk)

    def _act(self, client: Client, action: str, order: Any) -> Any:
        return client.post(
            changelist("order"),
            {"action": action, "_selected_action": [str(order.pk)]},
            follow=True,
        )

    def test_an_order_is_walked_from_pending_to_delivered(
        self, admin_client: Client, order: Any
    ) -> None:
        for action in ("mark_paid", "start_processing", "mark_sent", "mark_completed"):
            assert self._act(admin_client, action, order).status_code == 200

        order.refresh_from_db()
        assert order.status == OrderStatus.COMPLETED

    def test_a_step_out_of_turn_is_reported_rather_than_taken(
        self, admin_client: Client, order: Any
    ) -> None:
        response = self._act(admin_client, "mark_completed", order)

        order.refresh_from_db()
        assert response.status_code == 200
        assert order.status == OrderStatus.PENDING

    def test_cancelling_an_unpaid_order_puts_the_stock_back(
        self, admin_client: Client, order: Any, laptop: Product
    ) -> None:
        reserved = Product.objects.get(pk=laptop.pk).stock

        self._act(admin_client, "cancel_orders", order)

        order.refresh_from_db()
        assert order.status == OrderStatus.CANCELLED
        assert Product.objects.get(pk=laptop.pk).stock == reserved + 2

    def test_refunding_a_paid_order_puts_the_money_and_the_stock_back(
        self, admin_client: Client, order: Any, laptop: Product
    ) -> None:
        self._act(admin_client, "mark_paid", order)
        sold = Product.objects.get(pk=laptop.pk).stock

        self._act(admin_client, "refund_orders", order)

        order.refresh_from_db()
        assert order.status == OrderStatus.REFUNDED
        assert Product.objects.get(pk=laptop.pk).stock == sold + 2
        assert list(order.payments.values_list("status", flat=True)) == [PaymentStatus.REFUNDED]

    def test_cancelling_a_paid_order_is_refused(self, admin_client: Client, order: Any) -> None:
        """It would release the stock while the money stayed taken."""
        self._act(admin_client, "mark_paid", order)

        self._act(admin_client, "cancel_orders", order)

        order.refresh_from_db()
        assert order.status == OrderStatus.PAID

    def test_every_step_shows_up_on_the_order_form(self, admin_client: Client, order: Any) -> None:
        self._act(admin_client, "mark_paid", order)
        self._act(admin_client, "start_processing", order)

        page = admin_client.get(reverse("admin:shop_order_change", args=(order.pk,)))

        assert page.status_code == 200
        assert order.events.count() == 3
