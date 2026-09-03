"""The same shop over gRPC.

The catalogue calls are public, because reading a published catalogue needs no
credential. The basket calls read the caller out of ``authorization`` metadata,
so there is no field on any request that could name somebody else's basket.

``transactional_db`` throughout: the server answers on its own connection, and
rows sitting in the test's open transaction are not there yet as far as it is
concerned.
"""

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
