"""The storefront over HTTP: what a shopper can read, and what needs a login.

The catalogue half is public and must never mention a draft. The personal half
-- basket, likes, reviews -- is only ever the caller's own, and asking for
somebody else's is a 404 rather than a 403: a shop that answers "that basket
exists, but not for you" has told you something it should not have.
"""

from decimal import Decimal
from typing import Any

import pytest
from django.test import Client, override_settings

from apps.shop.models import (
    Category,
    Collection,
    Discount,
    Product,
    Review,
    ReviewStatus,
)
from apps.shop.tests.conftest import bearer

pytestmark = pytest.mark.django_db

SHOP = "/api/v1/shop"


@pytest.fixture
def client() -> Client:
    return Client()


def data(response: Any) -> Any:
    assert response.status_code == 200, response.content
    return response.json()["data"]


class TestCategories:
    def test_the_tree_comes_back_nested(
        self, client: Client, electronics: Category, laptops: Category
    ) -> None:
        [root] = data(client.get(f"{SHOP}/categories"))

        assert root["id"] == "electronics"
        assert [child["id"] for child in root["children"]] == ["laptops"]

    def test_a_hidden_category_is_not_listed(self, client: Client, electronics: Category) -> None:
        electronics.is_active = False
        electronics.save()

        assert data(client.get(f"{SHOP}/categories")) == []

    def test_a_count_is_a_categorys_own_products_rather_than_the_branchs(
        self, client: Client, laptops: Category, laptop: Product
    ) -> None:
        """Otherwise "Electronics (412)" sits above "Laptops (37)" and the sums
        do not add up on the shopper's screen."""
        [root] = data(client.get(f"{SHOP}/categories"))

        assert root["product_count"] == 0
        assert root["children"][0]["product_count"] == 1

    def test_counts_can_be_left_out(
        self, client: Client, laptops: Category, laptop: Product
    ) -> None:
        [root] = data(client.get(f"{SHOP}/categories?counts=false"))

        assert root["product_count"] is None

    def test_one_category_carries_its_breadcrumbs_and_its_attribute_schema(
        self, client: Client, laptops: Category
    ) -> None:
        category = data(client.get(f"{SHOP}/categories/laptops"))

        assert [crumb["slug"] for crumb in category["breadcrumbs"]] == ["electronics", "laptops"]
        assert [attribute["code"] for attribute in category["attributes"]] == [
            "warranty",
            "screen-size",
            "colour",
        ]

    def test_an_unknown_category_is_a_404(self, client: Client, db: None) -> None:
        assert client.get(f"{SHOP}/categories/nonesuch").status_code == 404


class TestProductListing:
    def test_a_draft_is_never_listed(self, client: Client, laptop: Product, draft: Product) -> None:
        page = data(client.get(f"{SHOP}/products"))

        assert [row["id"] for row in page["items"]] == ["featherbook-14"]
        assert page["total"] == 1

    def test_search_finds_a_product_by_its_brand(self, client: Client, laptop: Product) -> None:
        page = data(client.get(f"{SHOP}/products?search=acme"))

        assert [row["id"] for row in page["items"]] == ["featherbook-14"]

    def test_a_category_filter_includes_what_is_underneath_it(
        self, client: Client, laptop: Product, tshirt: Product
    ) -> None:
        page = data(client.get(f"{SHOP}/products?category=electronics"))

        assert [row["id"] for row in page["items"]] == ["featherbook-14"]

    def test_a_price_range_narrows_the_list(
        self, client: Client, laptop: Product, tshirt: Product
    ) -> None:
        page = data(client.get(f"{SHOP}/products?max_price=100"))

        assert [row["id"] for row in page["items"]] == ["plain-tee"]

    def test_a_brand_filter_narrows_the_list(
        self, client: Client, laptop: Product, tshirt: Product
    ) -> None:
        page = data(client.get(f"{SHOP}/products?brand=acme"))

        assert [row["id"] for row in page["items"]] == ["featherbook-14"]

    def test_an_attribute_filter_reads_code_colon_value(
        self, client: Client, laptop: Product
    ) -> None:
        page = data(client.get(f"{SHOP}/products?attribute=screen-size:14"))

        assert [row["id"] for row in page["items"]] == ["featherbook-14"]

    def test_an_attribute_filter_that_matches_nothing_returns_nothing(
        self, client: Client, laptop: Product
    ) -> None:
        page = data(client.get(f"{SHOP}/products?attribute=screen-size:17"))

        assert page["items"] == []

    def test_a_multi_choice_attribute_matches_one_of_the_answers(
        self, client: Client, laptop: Product, laptops: Category
    ) -> None:
        """Ticking "USB-C" means "has USB-C", not "has exactly USB-C"."""
        from apps.shop.attributes import AttributeType
        from apps.shop.models import CategoryAttribute, ProductAttribute

        ports = CategoryAttribute.objects.create(
            category=laptops,
            name="Ports",
            code="ports",
            attribute_type=AttributeType.MULTI_CHOICE,
            choices=["HDMI", "USB-C"],
        )
        ProductAttribute.objects.create(product=laptop, attribute=ports, value=["USB-C", "HDMI"])

        page = data(client.get(f"{SHOP}/products?attribute=ports:USB-C"))

        assert [row["id"] for row in page["items"]] == ["featherbook-14"]

    def test_a_multi_choice_match_is_not_a_loose_substring(
        self, client: Client, laptop: Product, laptops: Category
    ) -> None:
        """ "M" must not find a product answering "Medium"."""
        from apps.shop.attributes import AttributeType
        from apps.shop.models import CategoryAttribute, ProductAttribute

        sizes = CategoryAttribute.objects.create(
            category=laptops,
            name="Sizes",
            code="sizes",
            attribute_type=AttributeType.MULTI_CHOICE,
            choices=["M", "Medium"],
        )
        ProductAttribute.objects.create(product=laptop, attribute=sizes, value=["Medium"])

        assert data(client.get(f"{SHOP}/products?attribute=sizes:M"))["items"] == []

    def test_a_mistyped_filter_shows_the_unfiltered_list(
        self, client: Client, laptop: Product
    ) -> None:
        """A shopper editing the address bar should not get an error page."""
        page = data(client.get(f"{SHOP}/products?attribute=nonsense"))

        assert [row["id"] for row in page["items"]] == ["featherbook-14"]

    def test_on_sale_lists_only_what_a_campaign_reaches(
        self, client: Client, laptop: Product, tshirt: Product, sale: Discount
    ) -> None:
        page = data(client.get(f"{SHOP}/products?on_sale=true"))

        assert [row["id"] for row in page["items"]] == ["featherbook-14"]

    def test_sorting_by_price_puts_the_cheapest_first(
        self, client: Client, laptop: Product, tshirt: Product
    ) -> None:
        page = data(client.get(f"{SHOP}/products?sort=price_low"))

        assert [row["id"] for row in page["items"]] == ["plain-tee", "featherbook-14"]

    def test_an_unknown_sort_falls_back_to_the_default_order(
        self, client: Client, laptop: Product, tshirt: Product
    ) -> None:
        """Consistent with a mistyped filter: a typo shows a list, not an error page."""
        typo = data(client.get(f"{SHOP}/products?sort=sideways"))
        default = data(client.get(f"{SHOP}/products"))

        assert [row["id"] for row in typo["items"]] == [row["id"] for row in default["items"]]

    def test_a_limit_is_capped_rather_than_obeyed(self, client: Client, laptop: Product) -> None:
        with override_settings(SHOP_MAX_PAGE_SIZE=1, SHOP_PAGE_SIZE=1):
            page = data(client.get(f"{SHOP}/products?limit=5000"))

        assert page["limit"] == 1

    def test_a_page_carries_the_total_a_client_needs_to_print_showing_1_of_n(
        self, client: Client, laptop: Product, tshirt: Product
    ) -> None:
        page = data(client.get(f"{SHOP}/products?limit=1&offset=1"))

        assert page["total"] == 2
        assert page["limit"] == 1
        assert page["offset"] == 1
        assert len(page["items"]) == 1


class TestProductPage:
    def test_it_carries_everything_a_page_prints(self, client: Client, laptop: Product) -> None:
        product = data(client.get(f"{SHOP}/products/featherbook-14"))

        assert product["name"] == "Acme Featherbook 14"
        assert product["brand"] == "Acme"
        assert [image["url"] for image in product["images"]] == [
            "https://cdn.example.test/fb14.jpg"
        ]
        assert [answer["code"] for answer in product["attributes"]] == ["screen-size"]
        assert [crumb["slug"] for crumb in product["breadcrumbs"]] == ["electronics", "laptops"]

    def test_it_never_publishes_what_the_shop_paid(self, client: Client, laptop: Product) -> None:
        laptop.cost_price = Decimal("800.00")
        laptop.save()

        assert "cost_price" not in data(client.get(f"{SHOP}/products/featherbook-14"))

    def test_the_price_is_the_one_a_shopper_pays_now(
        self, client: Client, laptop: Product, sale: Discount
    ) -> None:
        price = data(client.get(f"{SHOP}/products/featherbook-14"))["price"]

        assert price["amount"] == "1080.00"
        assert price["base_amount"] == "1200.00"
        assert price["is_discounted"] is True
        assert price["discount"]["name"] == "Spring sale"

    def test_a_products_variants_come_with_their_own_prices(
        self, client: Client, tshirt: Product
    ) -> None:
        variants = data(client.get(f"{SHOP}/products/plain-tee"))["variants"]

        assert {variant["sku"]: variant["price"]["amount"] for variant in variants} == {
            "TEE-M": "20.00",
            "TEE-L": "22.00",
        }

    def test_a_draft_is_a_404(self, client: Client, draft: Product) -> None:
        assert client.get(f"{SHOP}/products/unannounced").status_code == 404

    def test_the_heart_is_empty_for_somebody_not_signed_in(
        self, client: Client, laptop: Product
    ) -> None:
        assert data(client.get(f"{SHOP}/products/featherbook-14"))["liked"] is False

    def test_the_heart_is_filled_in_for_whoever_liked_it(
        self, client: Client, laptop: Product, alice: Any
    ) -> None:
        client.put(f"{SHOP}/products/featherbook-14/like", **bearer(alice))

        product = data(client.get(f"{SHOP}/products/featherbook-14", **bearer(alice)))

        assert product["liked"] is True

    def test_a_view_is_counted(self, client: Client, laptop: Product) -> None:
        assert data(client.post(f"{SHOP}/products/featherbook-14/view"))["view_count"] == 1

    def test_related_products_come_from_the_same_category(
        self, client: Client, laptop: Product, laptops: Category
    ) -> None:
        Product.objects.create(
            category=laptops,
            name="Featherbook 16",
            slug="featherbook-16",
            sku="FB-16",
            price=Decimal("1500.00"),
            status="active",
            stock=1,
        )
        related = data(client.get(f"{SHOP}/products/featherbook-14/related"))

        assert [row["id"] for row in related] == ["featherbook-16"]


class TestListings:
    def test_the_keys_are_published_rather_than_hard_coded(self, client: Client, db: None) -> None:
        keys = [row["key"] for row in data(client.get(f"{SHOP}/listings"))]

        assert "bestsellers" in keys
        assert "on_sale" in keys

    def test_bestsellers_are_ordered_by_units_sold(
        self, client: Client, laptop: Product, tshirt: Product
    ) -> None:
        listing = data(client.get(f"{SHOP}/listings/bestsellers"))

        assert [row["id"] for row in listing["items"]] == ["featherbook-14", "plain-tee"]

    def test_on_sale_is_decided_by_the_clock_rather_than_a_column(
        self, client: Client, laptop: Product, tshirt: Product, sale: Discount
    ) -> None:
        listing = data(client.get(f"{SHOP}/listings/on_sale"))

        assert [row["id"] for row in listing["items"]] == ["featherbook-14"]

    def test_in_stock_leaves_out_what_cannot_be_bought(
        self, client: Client, laptop: Product
    ) -> None:
        laptop.stock = 0
        laptop.save()

        assert data(client.get(f"{SHOP}/listings/in_stock"))["items"] == []

    def test_an_unknown_list_says_which_ones_exist(self, client: Client, db: None) -> None:
        response = client.get(f"{SHOP}/listings/sideways")

        assert response.status_code == 404
        assert "bestsellers" in response.json()["errors"][0]


class TestCollections:
    def test_a_collection_keeps_the_order_somebody_chose(
        self, client: Client, staff_picks: Collection
    ) -> None:
        [collection] = data(client.get(f"{SHOP}/collections"))

        assert collection["id"] == "staff-picks"

    def test_one_collection_comes_with_its_products(
        self, client: Client, staff_picks: Collection
    ) -> None:
        collection = data(client.get(f"{SHOP}/collections/staff-picks"))

        assert [row["id"] for row in collection["products"]] == ["featherbook-14"]


class TestReviews:
    def test_writing_one_needs_a_credential(self, client: Client, laptop: Product) -> None:
        response = client.post(
            f"{SHOP}/products/featherbook-14/reviews",
            data={"rating": 5},
            content_type="application/json",
        )

        assert response.status_code == 401

    def test_a_review_moves_the_products_rating(
        self, client: Client, laptop: Product, alice: Any
    ) -> None:
        client.post(
            f"{SHOP}/products/featherbook-14/reviews",
            data={"rating": 4, "title": "Good", "body": "Quiet."},
            content_type="application/json",
            **bearer(alice),
        )
        laptop.refresh_from_db()

        assert laptop.rating_average == Decimal("4.00")

    def test_writing_twice_replaces_rather_than_refuses(
        self, client: Client, laptop: Product, alice: Any
    ) -> None:
        for rating in (2, 5):
            client.post(
                f"{SHOP}/products/featherbook-14/reviews",
                data={"rating": rating, "body": "Changed my mind."},
                content_type="application/json",
                **bearer(alice),
            )

        assert Review.objects.filter(product=laptop, user=alice).count() == 1
        assert Review.objects.get(product=laptop, user=alice).rating == 5

    def test_a_moderated_review_is_not_readable_yet(
        self, client: Client, laptop: Product, alice: Any
    ) -> None:
        with override_settings(SHOP_REVIEW_MODERATION=True):
            client.post(
                f"{SHOP}/products/featherbook-14/reviews",
                data={"rating": 4, "body": "Waiting."},
                content_type="application/json",
                **bearer(alice),
            )

            assert data(client.get(f"{SHOP}/products/featherbook-14/reviews"))["items"] == []

    def test_a_rating_outside_the_scale_is_a_400(
        self, client: Client, laptop: Product, alice: Any
    ) -> None:
        response = client.post(
            f"{SHOP}/products/featherbook-14/reviews",
            data={"rating": 9},
            content_type="application/json",
            **bearer(alice),
        )

        assert response.status_code == 400

    def test_taking_a_review_back_puts_the_rating_back(
        self, client: Client, laptop: Product, alice: Any
    ) -> None:
        client.post(
            f"{SHOP}/products/featherbook-14/reviews",
            data={"rating": 5, "body": "Great."},
            content_type="application/json",
            **bearer(alice),
        )
        removed = data(client.delete(f"{SHOP}/products/featherbook-14/reviews", **bearer(alice)))

        assert removed["rating_count"] == 0

    def test_a_review_never_publishes_an_address(
        self, client: Client, laptop: Product, alice: Any
    ) -> None:
        Review.objects.create(
            product=laptop, user=alice, rating=5, body="Great.", status=ReviewStatus.APPROVED
        )
        [review] = data(client.get(f"{SHOP}/products/featherbook-14/reviews"))["items"]

        assert review["author"] == "alice"
        assert "alice@example.test" not in str(review)

    def test_my_reviews_are_only_mine(
        self, client: Client, laptop: Product, alice: Any, bob: Any
    ) -> None:
        Review.objects.create(product=laptop, user=bob, rating=1, body="No.")

        assert data(client.get(f"{SHOP}/reviews/mine", **bearer(alice)))["items"] == []


class TestLikes:
    def test_liking_needs_a_credential(self, client: Client, laptop: Product) -> None:
        assert client.put(f"{SHOP}/products/featherbook-14/like").status_code == 401

    def test_liking_twice_is_the_same_answer(
        self, client: Client, laptop: Product, alice: Any
    ) -> None:
        first = data(client.put(f"{SHOP}/products/featherbook-14/like", **bearer(alice)))
        again = data(client.put(f"{SHOP}/products/featherbook-14/like", **bearer(alice)))

        assert first == again
        assert again["like_count"] == 1

    def test_unliking_something_never_liked_is_not_an_error(
        self, client: Client, laptop: Product, alice: Any
    ) -> None:
        answer = data(client.delete(f"{SHOP}/products/featherbook-14/like", **bearer(alice)))

        assert answer["liked"] is False
        assert answer["like_count"] == 0

    def test_favourites_list_what_this_account_liked(
        self, client: Client, laptop: Product, alice: Any
    ) -> None:
        client.put(f"{SHOP}/products/featherbook-14/like", **bearer(alice))

        page = data(client.get(f"{SHOP}/favourites", **bearer(alice)))

        assert [row["id"] for row in page["items"]] == ["featherbook-14"]

    def test_favourites_never_show_another_accounts(
        self, client: Client, laptop: Product, alice: Any, bob: Any
    ) -> None:
        client.put(f"{SHOP}/products/featherbook-14/like", **bearer(bob))

        assert data(client.get(f"{SHOP}/favourites", **bearer(alice)))["items"] == []


class TestCart:
    def _add(self, client: Client, user: Any, **payload: Any) -> Any:
        payload.setdefault("product", "featherbook-14")
        return client.post(
            f"{SHOP}/cart/items", data=payload, content_type="application/json", **bearer(user)
        )

    def test_reading_a_basket_needs_a_credential(self, client: Client, db: None) -> None:
        assert client.get(f"{SHOP}/cart").status_code == 401

    def test_a_basket_exists_from_the_first_read(self, client: Client, alice: Any) -> None:
        cart = data(client.get(f"{SHOP}/cart", **bearer(alice)))

        assert cart["items"] == []
        assert cart["total"] == "0"

    def test_adding_something_prices_the_line(
        self, client: Client, laptop: Product, alice: Any
    ) -> None:
        cart = data(self._add(client, alice, quantity=2))

        [line] = cart["items"]
        assert line["quantity"] == 2
        assert line["line_total"] == "2400.00"
        assert cart["unit_count"] == 2

    def test_adding_the_same_thing_twice_adds_up(
        self, client: Client, laptop: Product, alice: Any
    ) -> None:
        self._add(client, alice)
        cart = data(self._add(client, alice))

        assert len(cart["items"]) == 1
        assert cart["items"][0]["quantity"] == 2

    def test_a_basket_is_priced_now_rather_than_when_it_was_filled(
        self, client: Client, laptop: Product, alice: Any
    ) -> None:
        """The campaign starts after the thing is already in the basket."""
        self._add(client, alice)
        campaign = Discount.objects.create(name="Flash", value=Decimal("50"))
        campaign.products.add(laptop)

        cart = data(client.get(f"{SHOP}/cart", **bearer(alice)))

        assert cart["total"] == "600.00"
        assert cart["discount_total"] == "600.00"

    def test_more_than_the_shelf_holds_is_refused(
        self, client: Client, laptop: Product, alice: Any
    ) -> None:
        response = self._add(client, alice, quantity=99)

        assert response.status_code == 400
        assert "Only 5 left" in response.json()["errors"][0]

    def test_a_product_sold_in_variants_insists_on_one(
        self, client: Client, tshirt: Product, alice: Any
    ) -> None:
        response = self._add(client, alice, product="plain-tee")

        assert response.status_code == 400

    def test_a_variant_line_charges_the_variants_price(
        self, client: Client, tshirt: Product, alice: Any
    ) -> None:
        large = tshirt.variants.get(sku="TEE-L")
        large.stock = 2
        large.save()

        cart = data(self._add(client, alice, product="plain-tee", variant=str(large.pk)))

        assert cart["items"][0]["unit_price"]["amount"] == "22.00"

    def test_an_unknown_variant_is_a_404(self, client: Client, tshirt: Product, alice: Any) -> None:
        response = self._add(
            client, alice, product="plain-tee", variant="00000000-0000-0000-0000-000000000000"
        )

        assert response.status_code == 404

    def test_a_nonsense_variant_id_is_a_404_rather_than_a_500(
        self, client: Client, tshirt: Product, alice: Any
    ) -> None:
        assert self._add(client, alice, product="plain-tee", variant="banana").status_code == 404

    def test_the_per_line_ceiling_is_enforced(
        self, client: Client, laptop: Product, alice: Any
    ) -> None:
        laptop.stock = 500
        laptop.save()
        with override_settings(SHOP_MAX_ITEM_QUANTITY=3):
            response = self._add(client, alice, quantity=4)

        assert response.status_code == 400

    def test_setting_a_line_to_zero_removes_it(
        self, client: Client, laptop: Product, alice: Any
    ) -> None:
        item_id = data(self._add(client, alice))["items"][0]["id"]

        cart = data(
            client.patch(
                f"{SHOP}/cart/items/{item_id}",
                data={"quantity": 0},
                content_type="application/json",
                **bearer(alice),
            )
        )

        assert cart["items"] == []

    def test_a_line_in_somebody_elses_basket_does_not_exist(
        self, client: Client, laptop: Product, alice: Any, bob: Any
    ) -> None:
        """A 404, not a 403: "that exists but is not yours" is a leak."""
        item_id = data(self._add(client, alice))["items"][0]["id"]

        assert client.delete(f"{SHOP}/cart/items/{item_id}", **bearer(bob)).status_code == 404

    def test_emptying_the_basket_leaves_it_there(
        self, client: Client, laptop: Product, alice: Any
    ) -> None:
        self._add(client, alice)

        cart = data(client.delete(f"{SHOP}/cart", **bearer(alice)))

        assert cart["items"] == []
        assert cart["id"]
