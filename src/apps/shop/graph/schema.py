"""The shop's contribution to the project's GraphQL schema.

The same division as the routes. The catalogue is a set of queries anybody may
ask; the basket, the reviews and the likes are mutations whose caller is the
account they belong to, resolved from the credential rather than named in an
argument.

Every field is prefixed ``shop`` because the project merges each installed app's
``Query`` into one root type, and a bare ``products`` would collide with the
next app that sells something. The merge refuses collisions rather than silently
losing a field, so this is what keeps it quiet.

The resolvers are wrapped in ``@resolver``: the endpoint is one asynchronous
view -- the content app reads asynchronously and a schema cannot be half of each
-- and this service is ordinary synchronous Django, so it crosses over here,
once, rather than being written twice.
"""

from decimal import Decimal
from typing import Any

import strawberry
from strawberry.types import Info

from apps.shop.graph.types import (
    AddressInput,
    AddressRemovedType,
    AddressType,
    BrandType,
    CartType,
    CategoryNodeType,
    CategoryType,
    CollectionType,
    CouponPreviewType,
    InvoiceType,
    LikeType,
    ListingSummaryType,
    ListingType,
    OrderPageType,
    OrderType,
    ProductPageType,
    ProductSummaryType,
    ProductType,
    ProductViewType,
    ReviewPageType,
    ReviewRemovedType,
    ReviewType,
    SellerDetailType,
    SellerType,
    ShippingMethodType,
    address_type,
    brand_type,
    cart_type,
    category_node_type,
    category_type,
    collection_type,
    coupon_preview_type,
    invoice_type,
    listing_type,
    order_page_type,
    order_type,
    product_page_type,
    product_summary_type,
    product_type,
    review_page_type,
    review_type,
    seller_detail_type,
    seller_type,
    shipping_method_type,
)
from apps.shop.services import ShopNotFound, ShopRefused, shop_service
from infrastructure.common.errors import ApiError
from infrastructure.common.graph.errors import require_caller, resolver
from infrastructure.common.identity import caller
from infrastructure.common.responses import ResponseTitle


def _refuse(error: Exception) -> ApiError:
    """The service's two failures, in the vocabulary the rest of the project uses."""
    if isinstance(error, ShopNotFound):
        return ApiError(str(error), status=404, title=ResponseTitle.NOT_FOUND)
    return ApiError(str(error), status=400, title=ResponseTitle.VALIDATION_ERROR)


def _caller(info: Info[Any, Any]) -> Any:
    """The account this query proves it is. Demanded, for the shopper's half."""
    return require_caller(caller(info.context.request))


def _optional_caller(info: Info[Any, Any]) -> Any | None:
    """The same, where a credential is read if offered and never demanded."""
    return caller(info.context.request)


def _attribute_filters(pairs: list[str]) -> dict[str, str]:
    """Parse ``["colour:Red", "size:M"]`` into what the service takes."""
    filters: dict[str, str] = {}
    for pair in pairs:
        code, separator, value = pair.partition(":")
        if separator and code.strip() and value.strip():
            filters[code.strip()] = value.strip()
    return filters


@strawberry.type
class Query:
    @strawberry.field(description="Every visible category, nested, with product counts.")
    @resolver
    def shop_categories(self, info: Info[Any, Any], counts: bool = True) -> list[CategoryNodeType]:
        return [category_node_type(node) for node in shop_service.categories(with_counts=counts)]

    @strawberry.field(
        description="One category: its breadcrumb, its children, and the attributes "
        "its products answer."
    )
    @resolver
    def shop_category(self, info: Info[Any, Any], slug: str) -> CategoryType:
        try:
            return category_type(shop_service.category(slug))
        except ShopNotFound as error:
            raise _refuse(error) from None

    @strawberry.field(description="Every brand this shop stocks.")
    @resolver
    def shop_brands(self, info: Info[Any, Any]) -> list[BrandType]:
        return [brand_type(row) for row in shop_service.brands()]

    @strawberry.field(description="Everybody selling in this shop.")
    @resolver
    def shop_sellers(self, info: Info[Any, Any]) -> list[SellerType]:
        return [seller_type(row) for row in shop_service.sellers()]

    @strawberry.field(description="One seller, and how much of the catalogue they carry.")
    @resolver
    def shop_seller(self, info: Info[Any, Any], slug: str) -> SellerDetailType:
        try:
            return seller_detail_type(shop_service.seller(slug))
        except ShopNotFound as error:
            raise _refuse(error) from None

    @strawberry.field(description="Search and filter the catalogue. `attributes` reads code:value.")
    @resolver
    def shop_products(
        self,
        info: Info[Any, Any],
        search: str = "",
        category: str | None = None,
        brand: str | None = None,
        seller: str | None = None,
        tag: str | None = None,
        min_price: Decimal | None = None,
        max_price: Decimal | None = None,
        min_rating: int | None = None,
        in_stock: bool | None = None,
        on_sale: bool | None = None,
        featured: bool | None = None,
        attributes: list[str] | None = None,
        sort: str = "relevance",
        limit: int | None = None,
        offset: int = 0,
    ) -> ProductPageType:
        try:
            return product_page_type(
                shop_service.products(
                    search=search,
                    category=category,
                    brand=brand,
                    seller=seller,
                    tag=tag,
                    min_price=min_price,
                    max_price=max_price,
                    min_rating=min_rating,
                    in_stock=in_stock,
                    on_sale=on_sale,
                    featured=featured,
                    attributes=_attribute_filters(attributes or []),
                    sort=sort,
                    limit=limit,
                    offset=offset,
                )
            )
        except ShopNotFound as error:
            raise _refuse(error) from None

    @strawberry.field(description="One product: images, variants, specs, price and rating.")
    @resolver
    def shop_product(self, info: Info[Any, Any], slug: str) -> ProductType:
        try:
            return product_type(shop_service.product(slug, _optional_caller(info)))
        except ShopNotFound as error:
            raise _refuse(error) from None

    @strawberry.field(description="Other products in the same category.")
    @resolver
    def shop_related_products(
        self, info: Info[Any, Any], slug: str, limit: int = 8
    ) -> list[ProductSummaryType]:
        try:
            return [product_summary_type(row) for row in shop_service.related(slug, limit=limit)]
        except ShopNotFound as error:
            raise _refuse(error) from None

    @strawberry.field(description="What named lists this shop publishes.")
    @resolver
    def shop_listings(self, info: Info[Any, Any]) -> list[ListingSummaryType]:
        return [
            ListingSummaryType(key=row["key"], name=row["name"], description=row["description"])
            for row in shop_service.listings()
        ]

    @strawberry.field(
        description="One named list: bestsellers, popular, top_rated, newest, "
        "featured, on_sale, in_stock."
    )
    @resolver
    def shop_listing(
        self, info: Info[Any, Any], key: str, limit: int | None = None, offset: int = 0
    ) -> ListingType:
        try:
            return listing_type(shop_service.listing(key, limit=limit, offset=offset))
        except ShopNotFound as error:
            raise _refuse(error) from None

    @strawberry.field(description="The lists somebody arranged by hand.")
    @resolver
    def shop_collections(self, info: Info[Any, Any]) -> list[CollectionType]:
        return [collection_type(row) for row in shop_service.collections()]

    @strawberry.field(description="One curated collection, with its products in order.")
    @resolver
    def shop_collection(
        self, info: Info[Any, Any], slug: str, limit: int | None = None
    ) -> CollectionType:
        try:
            return collection_type(shop_service.collection(slug, limit=limit))
        except ShopNotFound as error:
            raise _refuse(error) from None

    @strawberry.field(description="A product's published reviews, newest first.")
    @resolver
    def shop_reviews(
        self, info: Info[Any, Any], slug: str, limit: int | None = None, offset: int = 0
    ) -> ReviewPageType:
        try:
            return review_page_type(shop_service.reviews(slug, limit=limit, offset=offset))
        except ShopNotFound as error:
            raise _refuse(error) from None

    @strawberry.field(
        description="Everything you have written, including what is still in the queue."
    )
    @resolver
    def shop_my_reviews(
        self, info: Info[Any, Any], limit: int | None = None, offset: int = 0
    ) -> ReviewPageType:
        return review_page_type(shop_service.my_reviews(_caller(info), limit=limit, offset=offset))

    @strawberry.field(description="Everything you have liked.")
    @resolver
    def shop_favourites(
        self, info: Info[Any, Any], limit: int | None = None, offset: int = 0
    ) -> ProductPageType:
        return product_page_type(shop_service.liked(_caller(info), limit=limit, offset=offset))

    @strawberry.field(description="Your basket, priced now rather than when you filled it.")
    @resolver
    def shop_cart(self, info: Info[Any, Any]) -> CartType:
        return cart_type(shop_service.cart(_caller(info)))

    @strawberry.field(description="Your orders, newest first.")
    @resolver
    def shop_orders(
        self, info: Info[Any, Any], limit: int | None = None, offset: int = 0
    ) -> OrderPageType:
        return order_page_type(shop_service.orders(_caller(info), limit=limit, offset=offset))

    @strawberry.field(description="One of your orders, at the prices you agreed to.")
    @resolver
    def shop_order(self, info: Info[Any, Any], number: str) -> OrderType:
        try:
            return order_type(shop_service.order(_caller(info), number))
        except ShopNotFound as error:
            raise _refuse(error) from None

    @strawberry.field(description="Your saved delivery addresses, the default one first.")
    @resolver
    def shop_addresses(self, info: Info[Any, Any]) -> list[AddressType]:
        return [address_type(row) for row in shop_service.addresses(_caller(info))]

    @strawberry.field(
        description=("Every delivery option, costed for your basket where you are signed in.")
    )
    @resolver
    def shop_shipping_methods(self, info: Info[Any, Any]) -> list[ShippingMethodType]:
        return [
            shipping_method_type(row)
            for row in shop_service.shipping_methods(_optional_caller(info))
        ]

    @strawberry.field(
        description="What a coupon code would take off your basket, without using it."
    )
    @resolver
    def shop_coupon_preview(self, info: Info[Any, Any], code: str) -> CouponPreviewType:
        try:
            return coupon_preview_type(shop_service.preview_coupon(_caller(info), code))
        except ShopRefused as error:
            raise _refuse(error) from None

    @strawberry.field(description="The invoice issued against one of your orders.")
    @resolver
    def shop_invoice(self, info: Info[Any, Any], number: str) -> InvoiceType:
        try:
            return invoice_type(shop_service.invoice(_caller(info), number))
        except ShopNotFound as error:
            raise _refuse(error) from None


@strawberry.type
class Mutation:
    @strawberry.mutation(description="Put something in your basket, or add to the line there.")
    @resolver
    def shop_add_to_cart(
        self,
        info: Info[Any, Any],
        product: str,
        variant: str | None = None,
        offer: str | None = None,
        quantity: int = 1,
    ) -> CartType:
        """``offer`` names the seller. Left out, the basket takes the one the
        product page was showing."""
        try:
            return cart_type(
                shop_service.add_to_cart(
                    _caller(info),
                    product,
                    variant_id=variant,
                    offer_id=offer,
                    quantity=quantity,
                )
            )
        except (ShopNotFound, ShopRefused) as error:
            raise _refuse(error) from None

    @strawberry.mutation(description="Set a line's quantity. Zero removes it.")
    @resolver
    def shop_set_cart_quantity(self, info: Info[Any, Any], item_id: str, quantity: int) -> CartType:
        try:
            return cart_type(shop_service.set_cart_quantity(_caller(info), item_id, quantity))
        except (ShopNotFound, ShopRefused) as error:
            raise _refuse(error) from None

    @strawberry.mutation(description="Take one line out of your basket.")
    @resolver
    def shop_remove_from_cart(self, info: Info[Any, Any], item_id: str) -> CartType:
        try:
            return cart_type(shop_service.remove_from_cart(_caller(info), item_id))
        except ShopNotFound as error:
            raise _refuse(error) from None

    @strawberry.mutation(description="Empty your basket.")
    @resolver
    def shop_clear_cart(self, info: Info[Any, Any]) -> CartType:
        return cart_type(shop_service.clear_cart(_caller(info)))

    @strawberry.mutation(description="Write your review of a product, or replace it.")
    @resolver
    def shop_review_product(
        self, info: Info[Any, Any], slug: str, rating: int, title: str = "", body: str = ""
    ) -> ReviewType:
        try:
            return review_type(
                shop_service.review_product(
                    _caller(info), slug, rating=rating, title=title, body=body
                )
            )
        except (ShopNotFound, ShopRefused) as error:
            raise _refuse(error) from None

    @strawberry.mutation(description="Take back your review.")
    @resolver
    def shop_delete_review(self, info: Info[Any, Any], slug: str) -> ReviewRemovedType:
        try:
            removed = shop_service.delete_review(_caller(info), slug)
        except ShopNotFound as error:
            raise _refuse(error) from None
        return ReviewRemovedType(
            product=removed["product"],
            rating_average=removed["rating_average"],
            rating_count=removed["rating_count"],
        )

    @strawberry.mutation(description="Like a product. Liking twice is the same answer.")
    @resolver
    def shop_like_product(self, info: Info[Any, Any], slug: str) -> LikeType:
        try:
            liked = shop_service.like(_caller(info), slug)
        except ShopNotFound as error:
            raise _refuse(error) from None
        return LikeType(
            product=liked["product"], liked=liked["liked"], like_count=liked["like_count"]
        )

    @strawberry.mutation(description="Unlike a product.")
    @resolver
    def shop_unlike_product(self, info: Info[Any, Any], slug: str) -> LikeType:
        try:
            unliked = shop_service.unlike(_caller(info), slug)
        except ShopNotFound as error:
            raise _refuse(error) from None
        return LikeType(
            product=unliked["product"], liked=unliked["liked"], like_count=unliked["like_count"]
        )

    @strawberry.mutation(description="Save somewhere to send an order to.")
    @resolver
    def shop_add_address(self, info: Info[Any, Any], address: AddressInput) -> AddressType:
        try:
            return address_type(
                shop_service.add_address(_caller(info), **strawberry.asdict(address))
            )
        except ShopRefused as error:
            raise _refuse(error) from None

    @strawberry.mutation(description="Edit a saved address. Placed orders keep their own copy.")
    @resolver
    def shop_update_address(
        self, info: Info[Any, Any], address_id: str, address: AddressInput
    ) -> AddressType:
        try:
            return address_type(
                shop_service.update_address(_caller(info), address_id, **strawberry.asdict(address))
            )
        except (ShopNotFound, ShopRefused) as error:
            raise _refuse(error) from None

    @strawberry.mutation(description="Choose the address a checkout offers first.")
    @resolver
    def shop_set_default_address(self, info: Info[Any, Any], address_id: str) -> AddressType:
        try:
            return address_type(shop_service.set_default_address(_caller(info), address_id))
        except (ShopNotFound, ShopRefused) as error:
            raise _refuse(error) from None

    @strawberry.mutation(description="Forget a saved address.")
    @resolver
    def shop_remove_address(self, info: Info[Any, Any], address_id: str) -> AddressRemovedType:
        try:
            shop_service.remove_address(_caller(info), address_id)
        except ShopNotFound as error:
            raise _refuse(error) from None
        return AddressRemovedType(deleted=True)

    @strawberry.mutation(
        description="Turn your basket into an order, an invoice and a payment to settle."
    )
    @resolver
    def shop_checkout(
        self,
        info: Info[Any, Any],
        address_id: str,
        shipping_method_id: str,
        coupon: str = "",
        note: str = "",
    ) -> OrderType:
        """Stock moves here rather than when the money arrives, so two shoppers
        on this page for the last one cannot both succeed."""
        caller_account = _caller(info)
        try:
            order = shop_service.checkout(
                caller_account,
                address_id=address_id,
                shipping_method_id=shipping_method_id,
                coupon_code=coupon,
                note=note,
            )
        except (ShopNotFound, ShopRefused) as error:
            raise _refuse(error) from None
        return order_type(shop_service.order(caller_account, order.number))

    @strawberry.mutation(description="Cancel an unpaid order and put its stock back.")
    @resolver
    def shop_cancel_order(self, info: Info[Any, Any], number: str) -> OrderType:
        caller_account = _caller(info)
        try:
            order = shop_service.cancel_order(caller_account, number)
        except (ShopNotFound, ShopRefused) as error:
            raise _refuse(error) from None
        return order_type(shop_service.order(caller_account, order.number))

    @strawberry.mutation(description="Settle a pending order's payment.")
    @resolver
    def shop_confirm_payment(
        self, info: Info[Any, Any], number: str, reference: str = ""
    ) -> OrderType:
        """The seam a payment provider's callback is pointed at.

        This starter wires up no gateway -- payments are created against the
        `manual` provider and settled by somebody in the admin looking at a bank
        statement. This is what a project points a real callback at once it has
        one, and it settles the order exactly the way the admin does.
        """
        caller_account = _caller(info)
        try:
            order = shop_service.confirm_payment(caller_account, number, reference=reference)
        except (ShopNotFound, ShopRefused) as error:
            raise _refuse(error) from None
        return order_type(shop_service.order(caller_account, order.number))

    @strawberry.mutation(description="Record that somebody looked at a product.")
    @resolver
    def shop_record_product_view(self, info: Info[Any, Any], slug: str) -> ProductViewType:
        try:
            return ProductViewType(product=slug, view_count=shop_service.view(slug))
        except ShopNotFound as error:
            raise _refuse(error) from None
