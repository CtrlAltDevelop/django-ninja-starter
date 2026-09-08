"""The same shop over gRPC.

The catalogue calls are public, because reading a published catalogue needs no
credential. The basket calls read the caller out of ``authorization`` metadata,
so there is no field on any request that could name somebody else's basket.

``transactional_db`` throughout: the server answers on its own connection, and
rows sitting in the test's open transaction are not there yet as far as it is
concerned.
"""

import json
from collections.abc import Callable
from decimal import Decimal
from typing import Any

import grpc
import pytest
from google.protobuf.empty_pb2 import Empty

from apps.shop.grpc import shop_pb2, shop_pb2_grpc
from apps.shop.models import Discount, Product, ProductOffer, Seller
from apps.shop.tests.conftest import access_token

Stub = shop_pb2_grpc.ShopControllerStub


@pytest.fixture
def stocked(transactional_db: None, laptop: Product) -> Product:
    """The shared `laptop` fixture, on a connection the server can also see."""
    return laptop


class TestCatalogue:
    def test_the_categories_arrive_flattened(
        self, stocked: Product, grpc_call: Callable[..., Any]
    ) -> None:
        """Protobuf has no recursive shorthand, so the tree is walked depth-first
        and each row names its parent."""
        reply = grpc_call(Stub, "Categories", Empty())

        assert [row.id for row in reply.categories] == ["electronics", "laptops"]
        assert reply.categories[1].parent == "electronics"

    def test_a_draft_is_not_listed(
        self, transactional_db: None, laptop: Product, draft: Product, grpc_call: Callable[..., Any]
    ) -> None:
        reply = grpc_call(Stub, "Products", shop_pb2.ProductsRequest())

        assert [row.id for row in reply.page.items] == ["featherbook-14"]
        assert reply.page.total == 1

    def test_search_narrows_the_catalogue(
        self, stocked: Product, grpc_call: Callable[..., Any]
    ) -> None:
        reply = grpc_call(Stub, "Products", shop_pb2.ProductsRequest(search="acme"))

        assert [row.id for row in reply.page.items] == ["featherbook-14"]

    def test_an_unknown_category_is_not_found(
        self, stocked: Product, grpc_call: Callable[..., Any]
    ) -> None:
        with pytest.raises(grpc.aio.AioRpcError) as refusal:
            grpc_call(Stub, "Products", shop_pb2.ProductsRequest(category="nonesuch"))

        assert refusal.value.code() is grpc.StatusCode.NOT_FOUND

    def test_a_limit_is_honoured(
        self,
        transactional_db: None,
        laptop: Product,
        tshirt: Product,
        grpc_call: Callable[..., Any],
    ) -> None:
        reply = grpc_call(Stub, "Products", shop_pb2.ProductsRequest(limit=1))

        assert len(reply.page.items) == 1
        assert reply.page.total == 2

    def test_one_product_comes_back_priced(
        self, stocked: Product, grpc_call: Callable[..., Any]
    ) -> None:
        reply = grpc_call(Stub, "Product", shop_pb2.ProductRequest(slug="featherbook-14"))

        assert reply.product.name == "Acme Featherbook 14"
        assert Decimal(reply.product.price.amount) == Decimal("1200.00")
        assert reply.product.price.is_discounted is False

    def test_a_discount_is_named_on_the_wire(
        self, transactional_db: None, laptop: Product, sale: Discount, grpc_call: Callable[..., Any]
    ) -> None:
        reply = grpc_call(Stub, "Product", shop_pb2.ProductRequest(slug="featherbook-14"))

        assert Decimal(reply.product.price.amount) == Decimal("1080.00")
        assert reply.product.price.discount_name == "Spring sale"
        assert reply.product.price.discount_percent_off == 10
        assert reply.product.price.discount_ends_at

    def test_a_price_with_no_campaign_carries_an_empty_end_date(
        self, stocked: Product, grpc_call: Callable[..., Any]
    ) -> None:
        """Protobuf has no null, so "no campaign" is the empty string."""
        reply = grpc_call(Stub, "Product", shop_pb2.ProductRequest(slug="featherbook-14"))

        assert reply.product.price.discount_ends_at == ""
        assert reply.product.price.discount_name == ""

    def test_a_draft_is_not_found(
        self, transactional_db: None, draft: Product, grpc_call: Callable[..., Any]
    ) -> None:
        with pytest.raises(grpc.aio.AioRpcError) as refusal:
            grpc_call(Stub, "Product", shop_pb2.ProductRequest(slug="unannounced"))

        assert refusal.value.code() is grpc.StatusCode.NOT_FOUND

    def test_the_named_lists_are_published(
        self, transactional_db: None, grpc_call: Callable[..., Any]
    ) -> None:
        reply = grpc_call(Stub, "Listings", Empty())

        assert "bestsellers" in [row.key for row in reply.listings]

    def test_one_list_comes_back(
        self,
        transactional_db: None,
        laptop: Product,
        tshirt: Product,
        grpc_call: Callable[..., Any],
    ) -> None:
        reply = grpc_call(Stub, "Listing", shop_pb2.ListingRequest(key="bestsellers"))

        assert reply.name == "Best sellers"
        assert [row.id for row in reply.page.items] == ["featherbook-14", "plain-tee"]

    def test_an_unknown_list_is_not_found(
        self, transactional_db: None, grpc_call: Callable[..., Any]
    ) -> None:
        with pytest.raises(grpc.aio.AioRpcError) as refusal:
            grpc_call(Stub, "Listing", shop_pb2.ListingRequest(key="sideways"))

        assert refusal.value.code() is grpc.StatusCode.NOT_FOUND


class TestTheBasket:
    def test_reading_it_without_a_credential_is_refused(
        self, transactional_db: None, grpc_call: Callable[..., Any]
    ) -> None:
        with pytest.raises(grpc.aio.AioRpcError) as refusal:
            grpc_call(Stub, "GetCart", Empty())

        assert refusal.value.code() is grpc.StatusCode.UNAUTHENTICATED

    def test_a_credential_in_metadata_opens_the_callers_own_basket(
        self, stocked: Product, alice: Any, grpc_call: Callable[..., Any]
    ) -> None:
        reply = grpc_call(Stub, "GetCart", Empty(), token=access_token(alice))

        assert reply.cart.item_count == 0
        assert reply.cart.id

    def test_something_added_over_grpc_is_priced_the_same_way(
        self, stocked: Product, alice: Any, grpc_call: Callable[..., Any]
    ) -> None:
        reply = grpc_call(
            Stub,
            "AddToCart",
            shop_pb2.AddToCartRequest(product="featherbook-14", quantity=2),
            token=access_token(alice),
        )

        assert reply.cart.item_count == 1
        assert Decimal(reply.cart.total) == Decimal("2400.00")

    def test_adding_without_a_credential_is_refused(
        self, stocked: Product, grpc_call: Callable[..., Any]
    ) -> None:
        with pytest.raises(grpc.aio.AioRpcError) as refusal:
            grpc_call(Stub, "AddToCart", shop_pb2.AddToCartRequest(product="featherbook-14"))

        assert refusal.value.code() is grpc.StatusCode.UNAUTHENTICATED

    def test_two_accounts_get_two_baskets(
        self, stocked: Product, alice: Any, bob: Any, grpc_call: Callable[..., Any]
    ) -> None:
        grpc_call(
            Stub,
            "AddToCart",
            shop_pb2.AddToCartRequest(product="featherbook-14"),
            token=access_token(alice),
        )

        theirs = grpc_call(Stub, "GetCart", Empty(), token=access_token(bob))

        assert theirs.cart.item_count == 0

    def test_more_than_the_shelf_holds_is_an_invalid_argument(
        self, stocked: Product, alice: Any, grpc_call: Callable[..., Any]
    ) -> None:
        with pytest.raises(grpc.aio.AioRpcError) as refusal:
            grpc_call(
                Stub,
                "AddToCart",
                shop_pb2.AddToCartRequest(product="featherbook-14", quantity=99),
                token=access_token(alice),
            )

        assert refusal.value.code() is grpc.StatusCode.INVALID_ARGUMENT

    def test_an_unknown_product_is_not_found(
        self, transactional_db: None, alice: Any, grpc_call: Callable[..., Any]
    ) -> None:
        with pytest.raises(grpc.aio.AioRpcError) as refusal:
            grpc_call(
                Stub,
                "AddToCart",
                shop_pb2.AddToCartRequest(product="nonesuch"),
                token=access_token(alice),
            )

        assert refusal.value.code() is grpc.StatusCode.NOT_FOUND

    def test_a_variant_line_carries_its_label(
        self, transactional_db: None, tshirt: Product, alice: Any, grpc_call: Callable[..., Any]
    ) -> None:
        medium = tshirt.variants.get(sku="TEE-M")

        reply = grpc_call(
            Stub,
            "AddToCart",
            shop_pb2.AddToCartRequest(product="plain-tee", variant=str(medium.pk)),
            token=access_token(alice),
        )

        assert reply.cart.items[0].variant_label == "size: M"


class TestSellersOverGrpc:
    def test_the_sellers_are_listed(
        self,
        transactional_db: None,
        acme_store: Seller,
        resellers: Seller,
        grpc_call: Callable[..., Any],
    ) -> None:
        reply = grpc_call(Stub, "Sellers", Empty())

        assert [row.id for row in reply.sellers] == ["acme-store", "bargain-bin"]

    def test_one_seller_says_how_much_they_carry(
        self,
        transactional_db: None,
        laptop: Product,
        undercut: ProductOffer,
        grpc_call: Callable[..., Any],
    ) -> None:
        reply = grpc_call(Stub, "Seller", shop_pb2.SellerRequest(slug="bargain-bin"))

        assert reply.seller.name == "Bargain Bin"
        assert reply.seller.product_count == 1

    def test_an_unknown_seller_is_not_found(
        self, transactional_db: None, grpc_call: Callable[..., Any]
    ) -> None:
        with pytest.raises(grpc.aio.AioRpcError) as refusal:
            grpc_call(Stub, "Seller", shop_pb2.SellerRequest(slug="nonesuch"))

        assert refusal.value.code() is grpc.StatusCode.NOT_FOUND

    def test_a_product_names_the_seller_it_is_listed_by(
        self,
        transactional_db: None,
        laptop: Product,
        acme_store: Seller,
        grpc_call: Callable[..., Any],
    ) -> None:
        laptop.seller = acme_store
        laptop.save()

        reply = grpc_call(Stub, "Product", shop_pb2.ProductRequest(slug="featherbook-14"))

        assert reply.product.seller == "Acme Store"
        assert reply.product.seller_slug == "acme-store"

    def test_a_product_with_no_seller_carries_an_empty_one(
        self, stocked: Product, grpc_call: Callable[..., Any]
    ) -> None:
        """Protobuf has no null, so "the shop itself" is the empty string."""
        reply = grpc_call(Stub, "Product", shop_pb2.ProductRequest(slug="featherbook-14"))

        assert reply.product.seller == ""

    def test_a_basket_line_names_its_seller(
        self,
        transactional_db: None,
        laptop: Product,
        undercut: ProductOffer,
        alice: Any,
        grpc_call: Callable[..., Any],
    ) -> None:
        reply = grpc_call(
            Stub,
            "AddToCart",
            shop_pb2.AddToCartRequest(product="featherbook-14"),
            token=access_token(alice),
        )

        assert reply.cart.items[0].seller == "Bargain Bin"
        assert Decimal(reply.cart.total) == Decimal("1100.00")

    def test_a_shopper_can_name_a_seller(
        self,
        transactional_db: None,
        laptop: Product,
        undercut: ProductOffer,
        acme_store: Seller,
        alice: Any,
        grpc_call: Callable[..., Any],
    ) -> None:
        dearer = ProductOffer.objects.create(
            product=laptop, seller=acme_store, price=Decimal("1250.00"), stock=1
        )

        reply = grpc_call(
            Stub,
            "AddToCart",
            shop_pb2.AddToCartRequest(product="featherbook-14", offer=str(dearer.pk)),
            token=access_token(alice),
        )

        assert Decimal(reply.cart.total) == Decimal("1250.00")


class TestOrdersOverGrpc:
    @pytest.fixture
    def placed(self, transactional_db: None, alice: Any, laptop: Product) -> Any:
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

    def test_the_list_needs_a_credential(
        self, transactional_db: None, grpc_call: Callable[..., Any]
    ) -> None:
        with pytest.raises(grpc.aio.AioRpcError) as refusal:
            grpc_call(Stub, "Orders", shop_pb2.OrdersRequest())

        assert refusal.value.code() is grpc.StatusCode.UNAUTHENTICATED

    def test_it_lists_this_accounts_orders(
        self, placed: Any, alice: Any, grpc_call: Callable[..., Any]
    ) -> None:
        reply = grpc_call(Stub, "Orders", shop_pb2.OrdersRequest(), token=access_token(alice))

        assert [row.number for row in reply.orders] == [placed.number]
        assert reply.total == 1

    def test_another_account_sees_none_of_them(
        self, placed: Any, bob: Any, grpc_call: Callable[..., Any]
    ) -> None:
        reply = grpc_call(Stub, "Orders", shop_pb2.OrdersRequest(), token=access_token(bob))

        assert list(reply.orders) == []

    def test_one_order_comes_back_off_its_snapshot(
        self, placed: Any, alice: Any, grpc_call: Callable[..., Any]
    ) -> None:
        reply = grpc_call(
            Stub,
            "Order",
            shop_pb2.OrderRequest(number=placed.number),
            token=access_token(alice),
        )

        assert reply.order.status_label == "Awaiting payment"
        assert reply.order.items[0].name == "Acme Featherbook 14"
        assert Decimal(reply.order.total) == placed.total

    def test_another_accounts_order_is_not_found(
        self, placed: Any, bob: Any, grpc_call: Callable[..., Any]
    ) -> None:
        with pytest.raises(grpc.aio.AioRpcError) as refusal:
            grpc_call(
                Stub,
                "Order",
                shop_pb2.OrderRequest(number=placed.number),
                token=access_token(bob),
            )

        assert refusal.value.code() is grpc.StatusCode.NOT_FOUND

    def test_the_invoice_comes_back_with_its_order(
        self, placed: Any, alice: Any, grpc_call: Callable[..., Any]
    ) -> None:
        reply = grpc_call(
            Stub,
            "Invoice",
            shop_pb2.InvoiceRequest(number=placed.number),
            token=access_token(alice),
        )

        assert reply.invoice.number == placed.invoice.number
        assert reply.invoice.order.number == placed.number
        assert reply.invoice.due_at == ""


class TestTheProductPageOverGrpc:
    """`Product` answers with the whole page, not the card `Products` returns.

    A door that could only hand back a summary would make a product page two
    round trips over gRPC and one over HTTP, which is the drift these tests
    exist to stop.
    """

    def test_the_page_carries_what_only_the_page_needs(
        self, stocked: Product, grpc_call: Callable[..., Any]
    ) -> None:
        reply = grpc_call(Stub, "Product", shop_pb2.ProductRequest(slug="featherbook-14"))

        product = reply.product
        assert product.description == "Fourteen inches of quiet aluminium."
        assert product.sku == "FB-14"
        assert [image.url for image in product.images] == ["https://cdn.example.test/fb14.jpg"]
        assert [crumb.slug for crumb in product.breadcrumbs] == ["electronics", "laptops"]
        assert product.meta.title == "Acme Featherbook 14"
        assert product.track_inventory is True
        assert product.stock == 5

    def test_an_attribute_value_travels_as_json_beside_its_type(
        self, stocked: Product, grpc_call: Callable[..., Any]
    ) -> None:
        """There is no protobuf type that fits "whatever this attribute stores",
        so the value is JSON and the type beside it says how to read it."""
        reply = grpc_call(Stub, "Product", shop_pb2.ProductRequest(slug="featherbook-14"))

        spec = reply.product.attributes[0]
        assert spec.code == "screen-size"
        assert spec.unit == "in"
        assert json.loads(spec.value_json) == 14

    def test_the_variant_attributes_are_the_ones_that_vary(
        self, stocked: Product, grpc_call: Callable[..., Any]
    ) -> None:
        reply = grpc_call(Stub, "Product", shop_pb2.ProductRequest(slug="featherbook-14"))

        assert [row.code for row in reply.product.variant_attributes] == ["colour"]

    def test_a_variants_options_travel_as_json(
        self, transactional_db: None, tshirt: Product, grpc_call: Callable[..., Any]
    ) -> None:
        reply = grpc_call(Stub, "Product", shop_pb2.ProductRequest(slug="plain-tee"))

        options = {
            variant.sku: json.loads(variant.options_json) for variant in reply.product.variants
        }
        assert options["TEE-M"] == {"size": "M"}

    def test_a_variant_carries_its_sellers_and_both_totals(
        self,
        transactional_db: None,
        tshirt: Product,
        resellers: Seller,
        grpc_call: Callable[..., Any],
    ) -> None:
        ProductOffer.objects.create(
            product=tshirt,
            seller=resellers,
            variant=tshirt.variants.get(sku="TEE-L"),
            price=Decimal("21.00"),
            stock=4,
        )

        reply = grpc_call(Stub, "Product", shop_pb2.ProductRequest(slug="plain-tee"))

        large = {variant.sku: variant for variant in reply.product.variants}["TEE-L"]
        assert (large.stock, large.total_stock) == (0, 4)
        assert large.in_stock is True
        assert large.seller_count == 1
        assert [row.seller.slug for row in large.sellers] == ["bargain-bin"]

    def test_a_variant_axis_says_which_values_are_left(
        self, transactional_db: None, tshirt: Product, grpc_call: Callable[..., Any]
    ) -> None:
        reply = grpc_call(Stub, "Product", shop_pb2.ProductRequest(slug="plain-tee"))

        [axis] = reply.product.variant_attributes
        assert [(row.value, row.in_stock) for row in axis.values] == [("M", True), ("L", False)]

    def test_an_absent_dimension_is_zero_rather_than_missing(
        self, stocked: Product, grpc_call: Callable[..., Any]
    ) -> None:
        """Protobuf has no null, and to a shipping estimate "no length" and a
        zero length are the same answer."""
        reply = grpc_call(Stub, "Product", shop_pb2.ProductRequest(slug="featherbook-14"))

        assert (reply.product.length_mm, reply.product.width_mm) == (0, 0)

    def test_nobody_selling_it_directly_leaves_sold_by_unset(
        self, stocked: Product, grpc_call: Callable[..., Any]
    ) -> None:
        reply = grpc_call(Stub, "Product", shop_pb2.ProductRequest(slug="featherbook-14"))

        assert reply.product.HasField("sold_by") is False
        assert reply.product.HasField("own_review") is False

    def test_a_seller_who_offers_it_is_counted(
        self,
        transactional_db: None,
        laptop: Product,
        undercut: ProductOffer,
        grpc_call: Callable[..., Any],
    ) -> None:
        reply = grpc_call(Stub, "Product", shop_pb2.ProductRequest(slug="featherbook-14"))

        assert [offer.seller.slug for offer in reply.product.offers] == ["bargain-bin"]
        assert reply.product.seller_count == 1

    def test_a_credential_fills_in_this_callers_own_relationship(
        self, stocked: Product, alice: Any, grpc_call: Callable[..., Any]
    ) -> None:
        """The page is public, but a caller who offered a token gets their own
        like and their own review on it rather than asking twice."""
        token = access_token(alice)
        grpc_call(Stub, "LikeProduct", shop_pb2.LikeProductRequest(slug="featherbook-14"), token)
        grpc_call(
            Stub,
            "ReviewProduct",
            shop_pb2.ReviewProductRequest(slug="featherbook-14", rating=5, title="Good"),
            token,
        )

        reply = grpc_call(
            Stub, "Product", shop_pb2.ProductRequest(slug="featherbook-14"), token=token
        )

        assert reply.product.liked is True
        assert reply.product.own_review.rating == 5

    def test_an_anonymous_caller_still_gets_the_page(
        self, stocked: Product, grpc_call: Callable[..., Any]
    ) -> None:
        reply = grpc_call(Stub, "Product", shop_pb2.ProductRequest(slug="featherbook-14"))

        assert reply.product.liked is False

    def test_related_products_come_from_the_same_category(
        self,
        transactional_db: None,
        laptop: Product,
        tshirt: Product,
        grpc_call: Callable[..., Any],
    ) -> None:
        """A sibling under `laptops` is related; a shirt under its own root is
        not, however well it sells."""
        sibling = Product.objects.create(
            category=laptop.category,
            name="Acme Featherbook 16",
            slug="featherbook-16",
            summary="The bigger one.",
            sku="FB-16",
            price=Decimal("1500.00"),
            status=laptop.status,
            stock=3,
        )

        reply = grpc_call(
            Stub, "RelatedProducts", shop_pb2.RelatedProductsRequest(slug="featherbook-14")
        )

        assert [row.id for row in reply.products] == [sibling.slug]

    def test_a_limit_bounds_the_related_row(
        self, transactional_db: None, laptop: Product, grpc_call: Callable[..., Any]
    ) -> None:
        for index in range(3):
            Product.objects.create(
                category=laptop.category,
                name=f"Sibling {index}",
                slug=f"sibling-{index}",
                summary="Another one.",
                sku=f"SIB-{index}",
                price=Decimal("999.00"),
                status=laptop.status,
                stock=1,
            )

        reply = grpc_call(
            Stub,
            "RelatedProducts",
            shop_pb2.RelatedProductsRequest(slug="featherbook-14", limit=2),
        )

        assert len(reply.products) == 2

    def test_a_view_is_counted_on_its_own_call(
        self, stocked: Product, grpc_call: Callable[..., Any]
    ) -> None:
        first = grpc_call(Stub, "RecordView", shop_pb2.RecordViewRequest(slug="featherbook-14"))
        second = grpc_call(Stub, "RecordView", shop_pb2.RecordViewRequest(slug="featherbook-14"))

        assert (first.view_count, second.view_count) == (1, 2)

    def test_viewing_something_that_is_not_there_is_not_found(
        self, stocked: Product, grpc_call: Callable[..., Any]
    ) -> None:
        with pytest.raises(grpc.aio.AioRpcError) as refusal:
            grpc_call(Stub, "RecordView", shop_pb2.RecordViewRequest(slug="nonesuch"))

        assert refusal.value.code() is grpc.StatusCode.NOT_FOUND


class TestBrowsingOverGrpc:
    def test_one_category_carries_its_tree_and_its_schema(
        self, stocked: Product, grpc_call: Callable[..., Any]
    ) -> None:
        reply = grpc_call(Stub, "Category", shop_pb2.CategoryRequest(slug="electronics"))

        assert reply.category.name == "Electronics"
        assert [child.slug for child in reply.category.children] == ["laptops"]
        assert [crumb.slug for crumb in reply.category.breadcrumbs] == ["electronics"]

    def test_a_categorys_attributes_declare_the_shape_of_its_products(
        self, stocked: Product, grpc_call: Callable[..., Any]
    ) -> None:
        reply = grpc_call(Stub, "Category", shop_pb2.CategoryRequest(slug="laptops"))

        schema = {row.code: row for row in reply.category.attributes}
        assert schema["screen-size"].type == "number"
        assert schema["screen-size"].required is True
        assert list(schema["colour"].choices) == ["Silver", "Space grey"]

    def test_an_unknown_category_is_not_found(
        self, stocked: Product, grpc_call: Callable[..., Any]
    ) -> None:
        with pytest.raises(grpc.aio.AioRpcError) as refusal:
            grpc_call(Stub, "Category", shop_pb2.CategoryRequest(slug="nonesuch"))

        assert refusal.value.code() is grpc.StatusCode.NOT_FOUND

    def test_the_brands_are_listed(self, stocked: Product, grpc_call: Callable[..., Any]) -> None:
        reply = grpc_call(Stub, "Brands", Empty())

        assert [row.slug for row in reply.brands] == ["acme"]
        assert reply.brands[0].website == "https://acme.example.test"

    def test_a_collection_keeps_the_order_somebody_arranged(
        self, transactional_db: None, staff_picks: Any, grpc_call: Callable[..., Any]
    ) -> None:
        reply = grpc_call(Stub, "Collection", shop_pb2.CollectionRequest(slug="staff-picks"))

        assert reply.collection.name == "Staff picks"
        assert [row.id for row in reply.collection.products] == ["featherbook-14"]

    def test_the_collection_list_leaves_the_products_out(
        self, transactional_db: None, staff_picks: Any, grpc_call: Callable[..., Any]
    ) -> None:
        """Twelve collections on a page must not mean twelve pages of products."""
        reply = grpc_call(Stub, "Collections", Empty())

        assert [row.slug for row in reply.collections] == ["staff-picks"]

    def test_an_unknown_collection_is_not_found(
        self, stocked: Product, grpc_call: Callable[..., Any]
    ) -> None:
        with pytest.raises(grpc.aio.AioRpcError) as refusal:
            grpc_call(Stub, "Collection", shop_pb2.CollectionRequest(slug="nonesuch"))

        assert refusal.value.code() is grpc.StatusCode.NOT_FOUND


class TestTheCatalogueFiltersOverGrpc:
    """Every filter the HTTP door has, answered by the same service."""

    def test_a_brand_narrows_it(self, stocked: Product, grpc_call: Callable[..., Any]) -> None:
        reply = grpc_call(Stub, "Products", shop_pb2.ProductsRequest(brand="acme"))

        assert [row.id for row in reply.page.items] == ["featherbook-14"]

    def test_a_brand_nobody_stocks_finds_nothing(
        self, stocked: Product, grpc_call: Callable[..., Any]
    ) -> None:
        reply = grpc_call(Stub, "Products", shop_pb2.ProductsRequest(brand="nonesuch"))

        assert reply.page.total == 0

    def test_a_tag_narrows_it(self, stocked: Product, grpc_call: Callable[..., Any]) -> None:
        reply = grpc_call(Stub, "Products", shop_pb2.ProductsRequest(tag="portable"))

        assert [row.id for row in reply.page.items] == ["featherbook-14"]

    def test_money_bounds_travel_as_strings(
        self,
        transactional_db: None,
        laptop: Product,
        tshirt: Product,
        grpc_call: Callable[..., Any],
    ) -> None:
        """A string because protobuf has no decimal, and a `double` bound would
        exclude the product priced exactly at it."""
        reply = grpc_call(Stub, "Products", shop_pb2.ProductsRequest(min_price="1000.00"))

        assert [row.id for row in reply.page.items] == ["featherbook-14"]

    def test_an_empty_bound_is_no_bound_rather_than_zero(
        self,
        transactional_db: None,
        laptop: Product,
        tshirt: Product,
        grpc_call: Callable[..., Any],
    ) -> None:
        """The whole reason the bounds are strings: an unset `max_price` must not
        read as "free only"."""
        reply = grpc_call(Stub, "Products", shop_pb2.ProductsRequest(max_price=""))

        assert reply.page.total == 2

    def test_a_bound_that_is_not_a_number_is_refused(
        self, stocked: Product, grpc_call: Callable[..., Any]
    ) -> None:
        with pytest.raises(grpc.aio.AioRpcError) as refusal:
            grpc_call(Stub, "Products", shop_pb2.ProductsRequest(min_price="cheap"))

        assert refusal.value.code() is grpc.StatusCode.INVALID_ARGUMENT

    def test_featured_narrows_it(self, stocked: Product, grpc_call: Callable[..., Any]) -> None:
        reply = grpc_call(Stub, "Products", shop_pb2.ProductsRequest(featured=True))

        assert [row.id for row in reply.page.items] == ["featherbook-14"]

    def test_an_attribute_filter_reads_code_colon_value(
        self, stocked: Product, grpc_call: Callable[..., Any]
    ) -> None:
        reply = grpc_call(Stub, "Products", shop_pb2.ProductsRequest(attributes=["screen-size:14"]))

        assert [row.id for row in reply.page.items] == ["featherbook-14"]

    def test_an_attribute_nothing_answers_finds_nothing(
        self, stocked: Product, grpc_call: Callable[..., Any]
    ) -> None:
        reply = grpc_call(Stub, "Products", shop_pb2.ProductsRequest(attributes=["screen-size:17"]))

        assert reply.page.total == 0

    def test_a_malformed_attribute_filter_is_ignored_rather_than_refused(
        self, stocked: Product, grpc_call: Callable[..., Any]
    ) -> None:
        """The same reading the query string gets: a mistyped filter shows the
        unfiltered list rather than an error."""
        reply = grpc_call(Stub, "Products", shop_pb2.ProductsRequest(attributes=["rubbish"]))

        assert reply.page.total == 1

    def test_on_sale_keeps_only_what_a_campaign_reaches(
        self,
        transactional_db: None,
        laptop: Product,
        tshirt: Product,
        sale: Discount,
        grpc_call: Callable[..., Any],
    ) -> None:
        reply = grpc_call(Stub, "Products", shop_pb2.ProductsRequest(on_sale=True))

        assert [row.id for row in reply.page.items] == ["featherbook-14"]


class TestReviewsAndLikesOverGrpc:
    def test_a_review_written_here_is_read_back_here(
        self, stocked: Product, alice: Any, grpc_call: Callable[..., Any]
    ) -> None:
        grpc_call(
            Stub,
            "ReviewProduct",
            shop_pb2.ReviewProductRequest(
                slug="featherbook-14", rating=4, title="Solid", body="Quiet enough."
            ),
            token=access_token(alice),
        )

        reply = grpc_call(Stub, "Reviews", shop_pb2.ReviewsRequest(slug="featherbook-14"))

        assert [row.title for row in reply.page.items] == ["Solid"]
        assert Decimal(reply.page.rating_average) == Decimal("4.00")
        assert reply.page.rating_count == 1

    def test_the_author_is_a_display_name_not_an_address(
        self, stocked: Product, alice: Any, grpc_call: Callable[..., Any]
    ) -> None:
        """Reviews are the most-scraped page on a shop, and an email published
        beside a name is a mailing list somebody else now owns."""
        grpc_call(
            Stub,
            "ReviewProduct",
            shop_pb2.ReviewProductRequest(slug="featherbook-14", rating=5),
            token=access_token(alice),
        )

        reply = grpc_call(Stub, "Reviews", shop_pb2.ReviewsRequest(slug="featherbook-14"))

        assert "@" not in reply.page.items[0].author

    def test_writing_a_second_one_edits_the_first(
        self, stocked: Product, alice: Any, grpc_call: Callable[..., Any]
    ) -> None:
        token = access_token(alice)
        grpc_call(
            Stub,
            "ReviewProduct",
            shop_pb2.ReviewProductRequest(slug="featherbook-14", rating=1, title="Bad"),
            token=token,
        )
        grpc_call(
            Stub,
            "ReviewProduct",
            shop_pb2.ReviewProductRequest(slug="featherbook-14", rating=5, title="Better"),
            token=token,
        )

        reply = grpc_call(Stub, "Reviews", shop_pb2.ReviewsRequest(slug="featherbook-14"))

        assert [row.title for row in reply.page.items] == ["Better"]
        assert reply.page.rating_count == 1

    def test_a_rating_outside_the_scale_is_refused(
        self, stocked: Product, alice: Any, grpc_call: Callable[..., Any]
    ) -> None:
        with pytest.raises(grpc.aio.AioRpcError) as refusal:
            grpc_call(
                Stub,
                "ReviewProduct",
                shop_pb2.ReviewProductRequest(slug="featherbook-14", rating=9),
                token=access_token(alice),
            )

        assert refusal.value.code() is grpc.StatusCode.INVALID_ARGUMENT

    def test_writing_one_without_a_credential_is_refused(
        self, stocked: Product, grpc_call: Callable[..., Any]
    ) -> None:
        with pytest.raises(grpc.aio.AioRpcError) as refusal:
            grpc_call(
                Stub,
                "ReviewProduct",
                shop_pb2.ReviewProductRequest(slug="featherbook-14", rating=5),
            )

        assert refusal.value.code() is grpc.StatusCode.UNAUTHENTICATED

    def test_my_reviews_are_this_callers_own(
        self, stocked: Product, alice: Any, bob: Any, grpc_call: Callable[..., Any]
    ) -> None:
        grpc_call(
            Stub,
            "ReviewProduct",
            shop_pb2.ReviewProductRequest(slug="featherbook-14", rating=3),
            token=access_token(alice),
        )

        mine = grpc_call(Stub, "MyReviews", shop_pb2.MyReviewsRequest(), token=access_token(alice))
        theirs = grpc_call(Stub, "MyReviews", shop_pb2.MyReviewsRequest(), token=access_token(bob))

        assert mine.page.total == 1
        assert theirs.page.total == 0

    def test_a_review_can_be_taken_back(
        self, stocked: Product, alice: Any, grpc_call: Callable[..., Any]
    ) -> None:
        token = access_token(alice)
        grpc_call(
            Stub,
            "ReviewProduct",
            shop_pb2.ReviewProductRequest(slug="featherbook-14", rating=5),
            token=token,
        )

        reply = grpc_call(
            Stub, "DeleteReview", shop_pb2.DeleteReviewRequest(slug="featherbook-14"), token=token
        )

        assert reply.rating_count == 0
        assert Decimal(reply.rating_average) == Decimal("0.00")

    def test_deleting_one_nobody_wrote_is_not_found(
        self, stocked: Product, alice: Any, grpc_call: Callable[..., Any]
    ) -> None:
        with pytest.raises(grpc.aio.AioRpcError) as refusal:
            grpc_call(
                Stub,
                "DeleteReview",
                shop_pb2.DeleteReviewRequest(slug="featherbook-14"),
                token=access_token(alice),
            )

        assert refusal.value.code() is grpc.StatusCode.NOT_FOUND

    def test_a_like_is_counted_once_however_often_it_is_sent(
        self, stocked: Product, alice: Any, grpc_call: Callable[..., Any]
    ) -> None:
        token = access_token(alice)
        first = grpc_call(
            Stub, "LikeProduct", shop_pb2.LikeProductRequest(slug="featherbook-14"), token=token
        )
        again = grpc_call(
            Stub, "LikeProduct", shop_pb2.LikeProductRequest(slug="featherbook-14"), token=token
        )

        assert (first.liked, first.like_count) == (True, 1)
        assert (again.liked, again.like_count) == (True, 1)

    def test_unliking_is_idempotent_too(
        self, stocked: Product, alice: Any, grpc_call: Callable[..., Any]
    ) -> None:
        token = access_token(alice)
        grpc_call(
            Stub, "LikeProduct", shop_pb2.LikeProductRequest(slug="featherbook-14"), token=token
        )

        reply = grpc_call(
            Stub, "UnlikeProduct", shop_pb2.UnlikeProductRequest(slug="featherbook-14"), token=token
        )
        again = grpc_call(
            Stub, "UnlikeProduct", shop_pb2.UnlikeProductRequest(slug="featherbook-14"), token=token
        )

        assert (reply.liked, reply.like_count) == (False, 0)
        assert again.like_count == 0

    def test_the_favourites_are_what_this_caller_marked(
        self, stocked: Product, alice: Any, bob: Any, grpc_call: Callable[..., Any]
    ) -> None:
        grpc_call(
            Stub,
            "LikeProduct",
            shop_pb2.LikeProductRequest(slug="featherbook-14"),
            token=access_token(alice),
        )

        mine = grpc_call(
            Stub, "Favourites", shop_pb2.FavouritesRequest(), token=access_token(alice)
        )
        theirs = grpc_call(
            Stub, "Favourites", shop_pb2.FavouritesRequest(), token=access_token(bob)
        )

        assert [row.id for row in mine.page.items] == ["featherbook-14"]
        assert theirs.page.total == 0

    def test_the_favourites_need_a_credential(
        self, stocked: Product, grpc_call: Callable[..., Any]
    ) -> None:
        with pytest.raises(grpc.aio.AioRpcError) as refusal:
            grpc_call(Stub, "Favourites", shop_pb2.FavouritesRequest())

        assert refusal.value.code() is grpc.StatusCode.UNAUTHENTICATED


class TestChangingTheBasketOverGrpc:
    """Adding was the only basket mutation this door had. These are the rest."""

    @pytest.fixture
    def filled(
        self, transactional_db: None, laptop: Product, alice: Any, grpc_call: Callable[..., Any]
    ) -> Any:
        reply = grpc_call(
            Stub,
            "AddToCart",
            shop_pb2.AddToCartRequest(product="featherbook-14", quantity=2),
            token=access_token(alice),
        )
        return reply.cart.items[0].id

    def test_a_quantity_is_set_rather_than_added_to(
        self, filled: str, alice: Any, grpc_call: Callable[..., Any]
    ) -> None:
        reply = grpc_call(
            Stub,
            "SetCartQuantity",
            shop_pb2.SetCartQuantityRequest(item_id=filled, quantity=3),
            token=access_token(alice),
        )

        assert reply.cart.items[0].quantity == 3
        assert Decimal(reply.cart.total) == Decimal("3600.00")

    def test_setting_it_to_nothing_removes_the_line(
        self, filled: str, alice: Any, grpc_call: Callable[..., Any]
    ) -> None:
        """Which is what a quantity box emptied out means to a shopper."""
        reply = grpc_call(
            Stub,
            "SetCartQuantity",
            shop_pb2.SetCartQuantityRequest(item_id=filled, quantity=0),
            token=access_token(alice),
        )

        assert reply.cart.item_count == 0

    def test_more_than_the_shelf_holds_is_refused(
        self, filled: str, alice: Any, grpc_call: Callable[..., Any]
    ) -> None:
        with pytest.raises(grpc.aio.AioRpcError) as refusal:
            grpc_call(
                Stub,
                "SetCartQuantity",
                shop_pb2.SetCartQuantityRequest(item_id=filled, quantity=99),
                token=access_token(alice),
            )

        assert refusal.value.code() is grpc.StatusCode.INVALID_ARGUMENT

    def test_a_line_can_be_removed(
        self, filled: str, alice: Any, grpc_call: Callable[..., Any]
    ) -> None:
        reply = grpc_call(
            Stub,
            "RemoveFromCart",
            shop_pb2.RemoveFromCartRequest(item_id=filled),
            token=access_token(alice),
        )

        assert reply.cart.item_count == 0
        assert Decimal(reply.cart.total) == Decimal("0.00")

    def test_somebody_elses_line_is_not_found(
        self, filled: str, bob: Any, grpc_call: Callable[..., Any]
    ) -> None:
        """A 404 rather than a 403: whose basket a line is in is not something
        another account gets told."""
        with pytest.raises(grpc.aio.AioRpcError) as refusal:
            grpc_call(
                Stub,
                "RemoveFromCart",
                shop_pb2.RemoveFromCartRequest(item_id=filled),
                token=access_token(bob),
            )

        assert refusal.value.code() is grpc.StatusCode.NOT_FOUND

    def test_the_basket_can_be_emptied(
        self, filled: str, alice: Any, grpc_call: Callable[..., Any]
    ) -> None:
        reply = grpc_call(Stub, "ClearCart", Empty(), token=access_token(alice))

        assert reply.cart.item_count == 0
        assert list(reply.cart.items) == []

    def test_emptying_it_needs_a_credential(
        self, transactional_db: None, grpc_call: Callable[..., Any]
    ) -> None:
        with pytest.raises(grpc.aio.AioRpcError) as refusal:
            grpc_call(Stub, "ClearCart", Empty())

        assert refusal.value.code() is grpc.StatusCode.UNAUTHENTICATED


class TestTheAddressBookOverGrpc:
    def test_one_address_can_be_read_on_its_own(
        self, transactional_db: None, address: Any, alice: Any, grpc_call: Callable[..., Any]
    ) -> None:
        reply = grpc_call(
            Stub,
            "GetAddress",
            shop_pb2.GetAddressRequest(address_id=str(address.pk)),
            token=access_token(alice),
        )

        assert reply.address.city == "Bristol"
        assert reply.address.is_default is True

    def test_somebody_elses_address_does_not_exist(
        self, transactional_db: None, address: Any, bob: Any, grpc_call: Callable[..., Any]
    ) -> None:
        with pytest.raises(grpc.aio.AioRpcError) as refusal:
            grpc_call(
                Stub,
                "GetAddress",
                shop_pb2.GetAddressRequest(address_id=str(address.pk)),
                token=access_token(bob),
            )

        assert refusal.value.code() is grpc.StatusCode.NOT_FOUND

    def test_an_edit_leaves_out_what_was_not_sent(
        self, transactional_db: None, address: Any, alice: Any, grpc_call: Callable[..., Any]
    ) -> None:
        """Protobuf has no unset scalar, so an empty field means "not sent" --
        which is the reading the service already commits to."""
        reply = grpc_call(
            Stub,
            "UpdateAddress",
            shop_pb2.UpdateAddressRequest(address_id=str(address.pk), city="Bath"),
            token=access_token(alice),
        )

        assert reply.address.city == "Bath"
        assert reply.address.line1 == "1 Example Street"
        assert reply.address.full_name == "Alice Example"

    def test_an_edit_does_not_disturb_the_default(
        self, transactional_db: None, address: Any, alice: Any, grpc_call: Callable[..., Any]
    ) -> None:
        """`is_default` is deliberately not on the request: a bool that cannot be
        told from unset would unset it on every unrelated edit."""
        reply = grpc_call(
            Stub,
            "UpdateAddress",
            shop_pb2.UpdateAddressRequest(address_id=str(address.pk), label="Office"),
            token=access_token(alice),
        )

        assert reply.address.is_default is True

    def test_editing_somebody_elses_is_not_found(
        self, transactional_db: None, address: Any, bob: Any, grpc_call: Callable[..., Any]
    ) -> None:
        with pytest.raises(grpc.aio.AioRpcError) as refusal:
            grpc_call(
                Stub,
                "UpdateAddress",
                shop_pb2.UpdateAddressRequest(address_id=str(address.pk), city="Bath"),
                token=access_token(bob),
            )

        assert refusal.value.code() is grpc.StatusCode.NOT_FOUND


class TestSettlingAnOrderOverGrpc:
    """The callback seam. This starter ships no gateway, so `manual` payments
    are settled by somebody reading a bank statement -- and this is what a real
    provider's callback is pointed at once there is one."""

    @pytest.fixture
    def placed(self, transactional_db: None, alice: Any, laptop: Product) -> Any:
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

    def test_confirming_the_payment_settles_the_order(
        self, placed: Any, alice: Any, grpc_call: Callable[..., Any]
    ) -> None:
        reply = grpc_call(
            Stub,
            "ConfirmPayment",
            shop_pb2.ConfirmPaymentRequest(number=placed.number, reference="bank-ref-1"),
            token=access_token(alice),
        )

        assert reply.order.payment_status == "succeeded"
        assert reply.order.status != "pending"

    def test_a_settled_order_can_no_longer_be_cancelled(
        self, placed: Any, alice: Any, grpc_call: Callable[..., Any]
    ) -> None:
        token = access_token(alice)
        grpc_call(
            Stub,
            "ConfirmPayment",
            shop_pb2.ConfirmPaymentRequest(number=placed.number),
            token=token,
        )

        with pytest.raises(grpc.aio.AioRpcError) as refusal:
            grpc_call(
                Stub, "CancelOrder", shop_pb2.CancelOrderRequest(number=placed.number), token=token
            )

        assert refusal.value.code() is grpc.StatusCode.INVALID_ARGUMENT

    def test_another_accounts_order_cannot_be_settled(
        self, placed: Any, bob: Any, grpc_call: Callable[..., Any]
    ) -> None:
        with pytest.raises(grpc.aio.AioRpcError) as refusal:
            grpc_call(
                Stub,
                "ConfirmPayment",
                shop_pb2.ConfirmPaymentRequest(number=placed.number),
                token=access_token(bob),
            )

        assert refusal.value.code() is grpc.StatusCode.NOT_FOUND

    def test_settling_needs_a_credential(self, placed: Any, grpc_call: Callable[..., Any]) -> None:
        with pytest.raises(grpc.aio.AioRpcError) as refusal:
            grpc_call(Stub, "ConfirmPayment", shop_pb2.ConfirmPaymentRequest(number=placed.number))

        assert refusal.value.code() is grpc.StatusCode.UNAUTHENTICATED
