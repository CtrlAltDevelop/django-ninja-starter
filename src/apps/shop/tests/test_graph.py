"""The same shop over GraphQL.

Same service as the routes, so the same answers: a draft is not there, a basket
is only ever the caller's own, and a refusal arrives with the status and title
the REST layer would have used -- in ``extensions``, since GraphQL answers 200
whatever happened.
"""

import json
from decimal import Decimal
from typing import Any

import pytest
from django.test import Client

from apps.shop.models import Category, Collection, Discount, Product, ProductOffer, Seller
from apps.shop.tests.conftest import access_token

pytestmark = pytest.mark.django_db

CATEGORIES = "{ shopCategories { id name productCount children { id } } }"
CATEGORY = """
query($slug: String!) {
  shopCategory(slug: $slug) {
    id
    breadcrumbs { slug }
    attributes { code isVariant }
  }
}
"""
PRODUCTS = """
query($search: String, $category: String, $onSale: Boolean, $sort: String, $limit: Int) {
  shopProducts(
    search: $search, category: $category, onSale: $onSale, sort: $sort, limit: $limit
  ) {
    total
    limit
    items { id name price { amount isDiscounted } }
  }
}
"""
PRODUCT = """
query($slug: String!) {
  shopProduct(slug: $slug) {
    id
    name
    liked
    price { amount baseAmount isDiscounted discount { name percentOff } }
    variants { sku price { amount } }
    images { url }
    attributes { code value }
  }
}
"""
LISTINGS = "{ shopListings { key name } }"
LISTING = """
query($key: String!) {
  shopListing(key: $key) { key name items { id } }
}
"""
COLLECTION = """
query($slug: String!) { shopCollection(slug: $slug) { id products { id } } }
"""
CART = "{ shopCart { id itemCount unitCount subtotal total items { product quantity } } }"
ADD_TO_CART = """
mutation($product: String!, $variant: String, $quantity: Int!) {
  shopAddToCart(product: $product, variant: $variant, quantity: $quantity) {
    itemCount
    total
    items { id quantity }
  }
}
"""
SET_QUANTITY = """
mutation($itemId: String!, $quantity: Int!) {
  shopSetCartQuantity(itemId: $itemId, quantity: $quantity) { itemCount unitCount }
}
"""
CLEAR_CART = "mutation { shopClearCart { itemCount } }"
REVIEW = """
mutation($slug: String!, $rating: Int!, $body: String) {
  shopReviewProduct(slug: $slug, rating: $rating, body: $body) { rating author status }
}
"""
LIKE = "mutation($slug: String!) { shopLikeProduct(slug: $slug) { liked likeCount } }"
UNLIKE = "mutation($slug: String!) { shopUnlikeProduct(slug: $slug) { liked likeCount } }"
FAVOURITES = "{ shopFavourites { items { id } } }"
RECORD_VIEW = "mutation($slug: String!) { shopRecordProductView(slug: $slug) { viewCount } }"
BRANDS = "{ shopBrands { id name website } }"
COLLECTIONS = "{ shopCollections { id name } }"
RELATED = """
query($slug: String!) { shopRelatedProducts(slug: $slug) { id } }
"""
REVIEWS = """
query($slug: String!) { shopReviews(slug: $slug) { total items { rating author } } }
"""
MY_REVIEWS = "{ shopMyReviews { total items { rating status } } }"
DELETE_REVIEW = """
mutation($slug: String!) { shopDeleteReview(slug: $slug) { product ratingCount } }
"""
REMOVE_FROM_CART = """
mutation($itemId: String!) { shopRemoveFromCart(itemId: $itemId) { itemCount } }
"""
FILTERED = """
query($attributes: [String!]) {
  shopProducts(attributes: $attributes) { items { id } }
}
"""


def graphql(query: str, user: Any = None, **variables: Any) -> dict[str, Any]:
    headers = {"HTTP_AUTHORIZATION": f"Bearer {access_token(user)}"} if user else {}
    response = Client().post(
        "/graphql",
        data={"query": query, "variables": variables},
        content_type="application/json",
        **headers,
    )
    assert response.status_code == 200, response.content
    return json.loads(response.content)


def refusal(body: dict[str, Any]) -> dict[str, Any]:
    assert body.get("errors"), body
    return dict(body["errors"][0]["extensions"])


class TestCatalogue:
    def test_the_tree_comes_back_nested(self, electronics: Category, laptops: Category) -> None:
        [root] = graphql(CATEGORIES)["data"]["shopCategories"]

        assert root["id"] == "electronics"
        assert [child["id"] for child in root["children"]] == ["laptops"]

    def test_a_category_carries_its_breadcrumb_and_schema(self, laptops: Category) -> None:
        category = graphql(CATEGORY, slug="laptops")["data"]["shopCategory"]

        assert [crumb["slug"] for crumb in category["breadcrumbs"]] == [
            "electronics",
            "laptops",
        ]
        assert {"code": "colour", "isVariant": True} in category["attributes"]

    def test_an_unknown_category_refuses_with_the_status_rest_would_have_used(
        self, db: None
    ) -> None:
        extensions = refusal(graphql(CATEGORY, slug="nonesuch"))

        assert extensions["title"] == "NOT_FOUND"
        assert extensions["status"] == 404

    def test_a_draft_is_not_listed(self, laptop: Product, draft: Product) -> None:
        page = graphql(PRODUCTS)["data"]["shopProducts"]

        assert [row["id"] for row in page["items"]] == ["featherbook-14"]
        assert page["total"] == 1

    def test_a_draft_is_not_readable(self, draft: Product) -> None:
        assert refusal(graphql(PRODUCT, slug="unannounced"))["status"] == 404

    def test_search_narrows_the_catalogue(self, laptop: Product, tshirt: Product) -> None:
        page = graphql(PRODUCTS, search="featherbook")["data"]["shopProducts"]

        assert [row["id"] for row in page["items"]] == ["featherbook-14"]

    def test_a_price_is_the_discounted_one(self, laptop: Product, sale: Discount) -> None:
        product = graphql(PRODUCT, slug="featherbook-14")["data"]["shopProduct"]

        assert Decimal(product["price"]["amount"]) == Decimal("1080.00")
        assert product["price"]["discount"]["name"] == "Spring sale"
        assert product["price"]["discount"]["percentOff"] == 10

    def test_variants_carry_their_own_prices(self, tshirt: Product) -> None:
        product = graphql(PRODUCT, slug="plain-tee")["data"]["shopProduct"]

        assert {variant["sku"]: variant["price"]["amount"] for variant in product["variants"]} == {
            "TEE-M": "20.00",
            "TEE-L": "22.00",
        }

    def test_on_sale_answers_the_same_question_the_route_does(
        self, laptop: Product, tshirt: Product, sale: Discount
    ) -> None:
        page = graphql(PRODUCTS, onSale=True)["data"]["shopProducts"]

        assert [row["id"] for row in page["items"]] == ["featherbook-14"]

    def test_the_named_lists_are_published_here_too(self, db: None) -> None:
        keys = [row["key"] for row in graphql(LISTINGS)["data"]["shopListings"]]

        assert "bestsellers" in keys

    def test_one_list_comes_back(self, laptop: Product, tshirt: Product) -> None:
        listing = graphql(LISTING, key="bestsellers")["data"]["shopListing"]

        assert [row["id"] for row in listing["items"]] == ["featherbook-14", "plain-tee"]

    def test_an_unknown_list_is_a_refusal(self, db: None) -> None:
        assert refusal(graphql(LISTING, key="sideways"))["status"] == 404

    def test_a_collection_comes_back_with_its_products(self, staff_picks: Collection) -> None:
        collection = graphql(COLLECTION, slug="staff-picks")["data"]["shopCollection"]

        assert [row["id"] for row in collection["products"]] == ["featherbook-14"]

    def test_a_view_is_counted(self, laptop: Product) -> None:
        assert (
            graphql(RECORD_VIEW, slug="featherbook-14")["data"]["shopRecordProductView"][
                "viewCount"
            ]
            == 1
        )


class TestTheBasket:
    def test_reading_it_needs_a_credential(self, db: None) -> None:
        assert refusal(graphql(CART))["title"] == "AUTHENTICATION_REQUIRED"

    def test_adding_to_it_needs_a_credential(self, laptop: Product) -> None:
        body = graphql(ADD_TO_CART, product="featherbook-14", quantity=1)

        assert refusal(body)["title"] == "AUTHENTICATION_REQUIRED"

    def test_something_added_over_graphql_is_priced_the_same_way(
        self, laptop: Product, alice: Any
    ) -> None:
        cart = graphql(ADD_TO_CART, alice, product="featherbook-14", quantity=2)["data"][
            "shopAddToCart"
        ]

        assert cart["itemCount"] == 1
        assert Decimal(cart["total"]) == Decimal("2400.00")

    def test_a_basket_filled_over_graphql_is_the_same_basket_the_route_reads(
        self, laptop: Product, alice: Any
    ) -> None:
        """One service behind three doors, so the basket cannot be per-transport."""
        graphql(ADD_TO_CART, alice, product="featherbook-14", quantity=1)

        response = Client().get(
            "/api/v1/shop/cart", HTTP_AUTHORIZATION=f"Bearer {access_token(alice)}"
        )

        assert response.json()["data"]["item_count"] == 1

    def test_more_than_the_shelf_holds_is_refused(self, laptop: Product, alice: Any) -> None:
        body = graphql(ADD_TO_CART, alice, product="featherbook-14", quantity=99)

        assert refusal(body)["status"] == 400

    def test_a_product_sold_in_variants_insists_on_one(self, tshirt: Product, alice: Any) -> None:
        body = graphql(ADD_TO_CART, alice, product="plain-tee", quantity=1)

        assert refusal(body)["status"] == 400

    def test_setting_a_line_to_zero_removes_it(self, laptop: Product, alice: Any) -> None:
        added = graphql(ADD_TO_CART, alice, product="featherbook-14", quantity=1)["data"][
            "shopAddToCart"
        ]
        item_id = added["items"][0]["id"]

        cart = graphql(SET_QUANTITY, alice, itemId=item_id, quantity=0)["data"][
            "shopSetCartQuantity"
        ]

        assert cart["itemCount"] == 0

    def test_a_line_in_somebody_elses_basket_does_not_exist(
        self, laptop: Product, alice: Any, bob: Any
    ) -> None:
        added = graphql(ADD_TO_CART, alice, product="featherbook-14", quantity=1)["data"][
            "shopAddToCart"
        ]
        item_id = added["items"][0]["id"]

        body = graphql(SET_QUANTITY, bob, itemId=item_id, quantity=5)

        assert refusal(body)["status"] == 404

    def test_emptying_it_works(self, laptop: Product, alice: Any) -> None:
        graphql(ADD_TO_CART, alice, product="featherbook-14", quantity=1)

        assert graphql(CLEAR_CART, alice)["data"]["shopClearCart"]["itemCount"] == 0


class TestReviewsAndLikes:
    def test_writing_a_review_needs_a_credential(self, laptop: Product) -> None:
        body = graphql(REVIEW, slug="featherbook-14", rating=5)

        assert refusal(body)["title"] == "AUTHENTICATION_REQUIRED"

    def test_a_review_is_written_and_attributed_to_a_display_name(
        self, laptop: Product, alice: Any
    ) -> None:
        review = graphql(REVIEW, alice, slug="featherbook-14", rating=4, body="Quiet.")["data"][
            "shopReviewProduct"
        ]

        assert review["rating"] == 4
        assert review["author"] == "alice"

    def test_a_rating_off_the_scale_is_refused(self, laptop: Product, alice: Any) -> None:
        body = graphql(REVIEW, alice, slug="featherbook-14", rating=0)

        assert refusal(body)["status"] == 400

    def test_liking_twice_is_the_same_answer(self, laptop: Product, alice: Any) -> None:
        first = graphql(LIKE, alice, slug="featherbook-14")["data"]["shopLikeProduct"]
        again = graphql(LIKE, alice, slug="featherbook-14")["data"]["shopLikeProduct"]

        assert first == again == {"liked": True, "likeCount": 1}

    def test_unliking_puts_the_count_back(self, laptop: Product, alice: Any) -> None:
        graphql(LIKE, alice, slug="featherbook-14")

        assert graphql(UNLIKE, alice, slug="featherbook-14")["data"]["shopUnlikeProduct"] == {
            "liked": False,
            "likeCount": 0,
        }

    def test_favourites_are_only_the_callers_own(
        self, laptop: Product, alice: Any, bob: Any
    ) -> None:
        graphql(LIKE, bob, slug="featherbook-14")

        assert graphql(FAVOURITES, alice)["data"]["shopFavourites"]["items"] == []

    def test_the_heart_is_filled_in_for_whoever_liked_it(self, laptop: Product, alice: Any) -> None:
        graphql(LIKE, alice, slug="featherbook-14")

        assert graphql(PRODUCT, alice, slug="featherbook-14")["data"]["shopProduct"]["liked"]

    def test_the_heart_is_empty_for_a_signed_out_shopper(self, laptop: Product) -> None:
        assert graphql(PRODUCT, slug="featherbook-14")["data"]["shopProduct"]["liked"] is False


class TestTheRestOfTheCatalogue:
    def test_the_brands_are_listed(self, laptop: Product) -> None:
        [brand] = graphql(BRANDS)["data"]["shopBrands"]

        assert brand["id"] == "acme"

    def test_the_collections_are_listed(self, staff_picks: Collection) -> None:
        [collection] = graphql(COLLECTIONS)["data"]["shopCollections"]

        assert collection["id"] == "staff-picks"

    def test_an_unknown_collection_is_a_refusal(self, db: None) -> None:
        assert refusal(graphql(COLLECTION, slug="nonesuch"))["status"] == 404

    def test_related_products_come_from_the_same_category(
        self, laptop: Product, laptops: Category
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

        rows = graphql(RELATED, slug="featherbook-14")["data"]["shopRelatedProducts"]

        assert [row["id"] for row in rows] == ["featherbook-16"]

    def test_related_products_of_an_unknown_product_is_a_refusal(self, db: None) -> None:
        assert refusal(graphql(RELATED, slug="nonesuch"))["status"] == 404

    def test_a_products_reviews_are_readable_without_a_credential(
        self, laptop: Product, alice: Any
    ) -> None:
        graphql(REVIEW, alice, slug="featherbook-14", rating=5, body="Great.")

        page = graphql(REVIEWS, slug="featherbook-14")["data"]["shopReviews"]

        assert page["total"] == 1
        assert page["items"][0]["author"] == "alice"

    def test_reviews_of_an_unknown_product_is_a_refusal(self, db: None) -> None:
        assert refusal(graphql(REVIEWS, slug="nonesuch"))["status"] == 404

    def test_an_attribute_filter_reads_code_colon_value(self, laptop: Product) -> None:
        page = graphql(FILTERED, attributes=["screen-size:14"])["data"]["shopProducts"]

        assert [row["id"] for row in page["items"]] == ["featherbook-14"]

    def test_a_mistyped_attribute_filter_is_ignored(self, laptop: Product) -> None:
        page = graphql(FILTERED, attributes=["nonsense"])["data"]["shopProducts"]

        assert [row["id"] for row in page["items"]] == ["featherbook-14"]


class TestTheShoppersOwnPages:
    def test_my_reviews_needs_a_credential(self, db: None) -> None:
        assert refusal(graphql(MY_REVIEWS))["title"] == "AUTHENTICATION_REQUIRED"

    def test_my_reviews_includes_what_is_still_in_the_queue(
        self, laptop: Product, alice: Any
    ) -> None:
        """You are the one person entitled to know your own review exists."""
        from django.test import override_settings

        with override_settings(SHOP_REVIEW_MODERATION=True):
            graphql(REVIEW, alice, slug="featherbook-14", rating=3, body="Waiting.")
            page = graphql(MY_REVIEWS, alice)["data"]["shopMyReviews"]

        assert page["total"] == 1
        assert page["items"][0]["status"] == "pending"

    def test_favourites_needs_a_credential(self, db: None) -> None:
        assert refusal(graphql(FAVOURITES))["title"] == "AUTHENTICATION_REQUIRED"

    def test_a_review_can_be_taken_back(self, laptop: Product, alice: Any) -> None:
        graphql(REVIEW, alice, slug="featherbook-14", rating=5, body="Great.")

        removed = graphql(DELETE_REVIEW, alice, slug="featherbook-14")["data"]["shopDeleteReview"]

        assert removed["ratingCount"] == 0

    def test_taking_back_a_review_nobody_wrote_is_a_refusal(
        self, laptop: Product, alice: Any
    ) -> None:
        assert refusal(graphql(DELETE_REVIEW, alice, slug="featherbook-14"))["status"] == 404

    def test_a_line_can_be_taken_out_of_the_basket(self, laptop: Product, alice: Any) -> None:
        added = graphql(ADD_TO_CART, alice, product="featherbook-14", quantity=1)["data"][
            "shopAddToCart"
        ]

        cart = graphql(REMOVE_FROM_CART, alice, itemId=added["items"][0]["id"])["data"][
            "shopRemoveFromCart"
        ]

        assert cart["itemCount"] == 0

    def test_a_line_that_is_not_in_any_basket_is_a_refusal(self, alice: Any) -> None:
        body = graphql(REMOVE_FROM_CART, alice, itemId="00000000-0000-0000-0000-000000000000")

        assert refusal(body)["status"] == 404


SELLERS = "{ shopSellers { id name city } }"
SELLER = "query($slug: String!) { shopSeller(slug: $slug) { id name productCount } }"
SOLD_BY = """
query($slug: String!) {
  shopProduct(slug: $slug) {
    price { amount }
    soldBy { slug name }
    sellerCount
    offers { sku leadTimeDays price { amount } seller { slug name } }
  }
}
"""
ORDERS = "{ shopOrders { total items { number status total invoice } } }"
ORDER = """
query($number: String!) {
  shopOrder(number: $number) {
    number
    status
    statusLabel
    total
    paymentStatus
    items { name seller quantity unitPrice }
  }
}
"""
INVOICE = """
query($number: String!) {
  shopInvoice(number: $number) { number order { number total } }
}
"""
ADD_FROM_SELLER = """
mutation($product: String!, $offer: String) {
  shopAddToCart(product: $product, offer: $offer, quantity: 1) {
    total
    items { seller offer }
  }
}
"""


class TestSellersOverGraphql:
    def test_the_sellers_are_listed(self, acme_store: Seller, resellers: Seller) -> None:
        rows = graphql(SELLERS)["data"]["shopSellers"]

        assert [row["id"] for row in rows] == ["acme-store", "bargain-bin"]

    def test_one_seller_says_how_much_they_carry(
        self, laptop: Product, undercut: ProductOffer
    ) -> None:
        seller = graphql(SELLER, slug="bargain-bin")["data"]["shopSeller"]

        assert seller["productCount"] == 1

    def test_an_unknown_seller_is_a_refusal(self, db: None) -> None:
        assert refusal(graphql(SELLER, slug="nonesuch"))["status"] == 404

    def test_a_product_names_who_sells_it_and_who_else_could(
        self, laptop: Product, undercut: ProductOffer, acme_store: Seller
    ) -> None:
        laptop.seller = acme_store
        laptop.save()

        product = graphql(SOLD_BY, slug="featherbook-14")["data"]["shopProduct"]

        assert product["soldBy"]["slug"] == "acme-store"
        assert product["sellerCount"] == 2
        assert product["offers"][0]["seller"]["name"] == "Bargain Bin"
        assert Decimal(product["price"]["amount"]) == Decimal("1100.00")

    def test_a_basket_line_names_its_seller(
        self, laptop: Product, undercut: ProductOffer, alice: Any
    ) -> None:
        cart = graphql(ADD_FROM_SELLER, alice, product="featherbook-14")["data"]["shopAddToCart"]

        assert cart["items"][0]["seller"] == "Bargain Bin"
        assert Decimal(cart["total"]) == Decimal("1100.00")

    def test_a_shopper_can_name_a_seller(
        self, laptop: Product, undercut: ProductOffer, acme_store: Seller, alice: Any
    ) -> None:
        dearer = ProductOffer.objects.create(
            product=laptop, seller=acme_store, price=Decimal("1250.00"), stock=1
        )

        cart = graphql(ADD_FROM_SELLER, alice, product="featherbook-14", offer=str(dearer.pk))[
            "data"
        ]["shopAddToCart"]

        assert Decimal(cart["total"]) == Decimal("1250.00")

    def test_an_offer_of_another_product_is_a_refusal(
        self, laptop: Product, tshirt: Product, resellers: Seller, alice: Any
    ) -> None:
        theirs = ProductOffer.objects.create(
            product=tshirt,
            seller=resellers,
            variant=tshirt.variants.get(sku="TEE-M"),
            price=Decimal("15.00"),
            stock=1,
        )
        body = graphql(ADD_FROM_SELLER, alice, product="featherbook-14", offer=str(theirs.pk))

        assert refusal(body)["status"] == 404


class TestOrdersOverGraphql:
    @pytest.fixture
    def placed(self, alice: Any, laptop: Product) -> Any:
        from apps.shop.models import Address, ShippingMethod
        from apps.shop.services import shop_service

        address = Address.objects.create(
            user=alice,
            full_name="Alice Example",
            phone="+441234567890",
            country="GB",
            city="Bristol",
            postal_code="BS1 4ST",
            line1="1 Example Street",
        )
        shipping = ShippingMethod.objects.create(name="Standard", price=Decimal("5.00"))
        shop_service.add_to_cart(alice, "featherbook-14")
        return shop_service.checkout(alice, address_id=address.pk, shipping_method_id=shipping.pk)

    def test_the_list_needs_a_credential(self, db: None) -> None:
        assert refusal(graphql(ORDERS))["title"] == "AUTHENTICATION_REQUIRED"

    def test_it_lists_this_accounts_orders(self, placed: Any, alice: Any) -> None:
        page = graphql(ORDERS, alice)["data"]["shopOrders"]

        assert [row["number"] for row in page["items"]] == [placed.number]
        assert page["items"][0]["invoice"] == placed.invoice.number

    def test_it_never_lists_another_accounts(self, placed: Any, bob: Any) -> None:
        assert graphql(ORDERS, bob)["data"]["shopOrders"]["items"] == []

    def test_one_order_reads_off_its_snapshot(self, placed: Any, alice: Any) -> None:
        order = graphql(ORDER, alice, number=placed.number)["data"]["shopOrder"]

        assert order["statusLabel"] == "Awaiting payment"
        assert order["paymentStatus"] == "pending"
        assert order["items"][0]["name"] == "Acme Featherbook 14"

    def test_another_accounts_order_is_a_refusal(self, placed: Any, bob: Any) -> None:
        assert refusal(graphql(ORDER, bob, number=placed.number))["status"] == 404

    def test_the_invoice_comes_back_with_its_order(self, placed: Any, alice: Any) -> None:
        document = graphql(INVOICE, alice, number=placed.number)["data"]["shopInvoice"]

        assert document["number"] == placed.invoice.number
        assert document["order"]["number"] == placed.number

    def test_another_accounts_invoice_is_a_refusal(self, placed: Any, bob: Any) -> None:
        assert refusal(graphql(INVOICE, bob, number=placed.number))["status"] == 404
