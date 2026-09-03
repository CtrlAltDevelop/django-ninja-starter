"""The storefront over gRPC.

Catalogue calls are public. Basket calls derive their user from invocation
metadata, so a client can never ask for another customer's cart.
"""

from __future__ import annotations

from typing import Any

from asgiref.sync import sync_to_async
from django_socio_grpc import generics
from django_socio_grpc.decorators import grpc_action

from apps.shop.grpc.serializers import (
    Cart,
    CategorySummary,
    Invoice,
    ListingSummary,
    Order,
    ProductPage,
    ProductSummary,
    Seller,
)
from apps.shop.services import ShopNotFound, ShopRefused, shop_service
from infrastructure.common.errors import ApiError
from infrastructure.common.grpc.errors import action, require_caller
from infrastructure.common.identity import grpc_caller
from infrastructure.common.responses import ResponseTitle


def _pb2() -> Any:
    from apps.shop.grpc import shop_pb2

    return shop_pb2


def _missing(error: ShopNotFound) -> ApiError:
    return ApiError(str(error), status=404, title=ResponseTitle.NOT_FOUND)


def _refused(error: ShopRefused) -> ApiError:
    return ApiError(str(error), status=400, title=ResponseTitle.VALIDATION_ERROR)


def _price(value: dict[str, Any]) -> Any:
    """One price, with the campaign behind it flattened onto the same message.

    The payload nests the campaign under ``discount`` and leaves it ``None``
    when there is none. Protobuf has neither a null nor a cheap optional
    submessage, so the fields are flattened here and an absent campaign is the
    empty string -- which is why this reads the nested dictionary rather than
    the flat keys the message happens to use.
    """
    pb2 = _pb2()
    discount = value.get("discount") or {}
    ends_at = discount.get("ends_at")
    return pb2.Price(
        amount=str(value["amount"]),
        base_amount=str(value["base_amount"]),
        currency=value["currency"],
        is_discounted=value["is_discounted"],
        discount_id=str(discount.get("id") or ""),
        discount_name=discount.get("name") or "",
        discount_kind=discount.get("kind") or "",
        discount_value=str(discount.get("value") or ""),
        discount_amount_off=str(discount.get("amount_off") or ""),
        discount_percent_off=discount.get("percent_off") or 0,
        discount_ends_at=ends_at.isoformat() if ends_at else "",
    )


def _product(row: dict[str, Any]) -> Any:
    pb2 = _pb2()
    return pb2.ProductSummary(
        id=row["id"],
        slug=row["slug"],
        name=row["name"],
        subtitle=row["subtitle"],
        summary=row["summary"],
        image=row["image"],
        brand=row["brand"] or "",
        brand_slug=row["brand_slug"] or "",
        seller=row.get("seller") or "",
        seller_slug=row.get("seller_slug") or "",
        category=row["category"],
        category_name=row["category_name"],
        price=_price(row["price"]),
        compare_at_price=str(row["compare_at_price"] or ""),
        in_stock=row["in_stock"],
        has_variants=row["has_variants"],
        is_featured=row["is_featured"],
        rating_average=str(row["rating_average"]),
        rating_count=row["rating_count"],
        like_count=row["like_count"],
        sales_count=row["sales_count"],
        tags=row["tags"],
        created_at=row["created_at"].isoformat(),
    )


def _page(row: dict[str, Any]) -> Any:
    return _pb2().ProductPage(
        items=[_product(item) for item in row["items"]],
        total=row["total"],
        limit=row["limit"],
        offset=row["offset"],
    )


def _cart(row: dict[str, Any]) -> Any:
    pb2 = _pb2()
    return pb2.Cart(
        id=row["id"],
        currency=row["currency"],
        item_count=row["item_count"],
        subtotal=str(row["subtotal"]),
        discount_total=str(row["discount_total"]),
        total=str(row["total"]),
        updated_at=row["updated_at"].isoformat(),
        items=[
            pb2.CartItem(
                id=item["id"],
                product=item["product"],
                name=item["name"],
                image=item["image"],
                variant=item["variant"] or "",
                variant_label=item["variant_label"] or "",
                offer=item.get("offer") or "",
                seller=item.get("seller") or "",
                quantity=item["quantity"],
                unit_price=_price(item["unit_price"]),
                line_total=str(item["line_total"]),
                in_stock=item["in_stock"],
            )
            for item in row["items"]
        ],
    )


def _seller(row: dict[str, Any]) -> Any:
    """One seller. ``product_count`` is zero where the caller did not ask for it."""
    return _pb2().Seller(
        id=row["id"],
        name=row["name"],
        slug=row["slug"],
        description=row["description"],
        logo=row["logo"],
        city=row["city"],
        product_count=row.get("product_count") or 0,
    )


def _offer(row: dict[str, Any]) -> Any:
    return _pb2().Offer(
        id=row["id"],
        seller=_seller(row["seller"]),
        variant=row.get("variant") or "",
        sku=row["sku"],
        condition=row["condition"],
        lead_time_days=row["lead_time_days"],
        stock=row["stock"],
        in_stock=row["in_stock"],
        price=_price(row["price"]),
    )


def _order(row: dict[str, Any]) -> Any:
    """One order. Protobuf has no null, so what is not set is the empty string."""
    pb2 = _pb2()
    return pb2.Order(
        number=row["number"],
        status=row["status"],
        status_label=row["status_label"],
        currency=row["currency"],
        placed_at=row["placed_at"].isoformat(),
        items=[
            pb2.OrderLine(
                product=item.get("product") or "",
                name=item["name"],
                seller=item.get("seller") or "",
                sku=item["sku"],
                quantity=item["quantity"],
                unit_price=str(item["unit_price"]),
                tax_rate=str(item["tax_rate"]),
                line_total=str(item["line_total"]),
            )
            for item in row["items"]
        ],
        subtotal=str(row["subtotal"]),
        coupon=row.get("coupon") or "",
        coupon_discount=str(row["coupon_discount"]),
        shipping_method=row.get("shipping_method") or "",
        shipping_total=str(row["shipping_total"]),
        tax_total=str(row["tax_total"]),
        total=str(row["total"]),
        note=row["note"],
        payment_status=row.get("payment_status") or "",
        invoice=row.get("invoice") or "",
    )


def _invoice(row: dict[str, Any]) -> Any:
    due_at = row.get("due_at")
    return _pb2().Invoice(
        number=row["number"],
        issued_at=row["issued_at"].isoformat(),
        due_at=due_at.isoformat() if due_at else "",
        notes=row["notes"],
        order=_order(row["order"]),
    )


class ShopService(generics.GenericService):
    """Search, merchandising lists, reviews and the authenticated basket."""

    @grpc_action(
        request=[],
        response=[{"name": "categories", "cardinality": "repeated", "type": CategorySummary}],
        response_name="CategoryList",
    )
    @action
    async def Categories(self, request: Any, context: Any) -> Any:
        rows = await sync_to_async(shop_service.categories)(with_counts=True)
        flat: list[dict] = []

        def walk(nodes: list[dict]) -> None:
            for node in nodes:
                flat.append(node)
                walk(node["children"])

        walk(rows)
        pb2 = _pb2()
        return pb2.CategoryList(
            categories=[
                pb2.CategorySummary(
                    id=row["id"],
                    name=row["name"],
                    slug=row["slug"],
                    description=row["description"],
                    image=row["image"],
                    icon=row["icon"],
                    parent=row["parent"] or "",
                    product_count=row["product_count"] or 0,
                )
                for row in flat
            ]
        )

    @grpc_action(
        request=[
            {"name": "search", "type": "string"},
            {"name": "category", "type": "string"},
            {"name": "seller", "type": "string"},
            {"name": "sort", "type": "string"},
            {"name": "limit", "type": "int32"},
            {"name": "offset", "type": "int32"},
        ],
        request_name="ProductsRequest",
        response=[{"name": "page", "type": ProductPage}],
        response_name="ProductsResult",
    )
    @action
    async def Products(self, request: Any, context: Any) -> Any:
        try:
            page = await sync_to_async(shop_service.products)(
                search=request.search,
                category=request.category or None,
                seller=request.seller or None,
                sort=request.sort or "relevance",
                limit=request.limit or None,
                offset=request.offset,
            )
        except ShopNotFound as error:
            raise _missing(error) from None
        return _pb2().ProductsResult(page=_page(page))

    @grpc_action(
        request=[{"name": "slug", "type": "string"}],
        request_name="ProductRequest",
        response=[{"name": "product", "type": ProductSummary}],
        response_name="ProductResult",
    )
    @action
    async def Product(self, request: Any, context: Any) -> Any:
        try:
            row = await sync_to_async(shop_service.product)(request.slug)
        except ShopNotFound as error:
            raise _missing(error) from None
        return _pb2().ProductResult(product=_product(row))

    @grpc_action(
        request=[],
        response=[{"name": "listings", "cardinality": "repeated", "type": ListingSummary}],
        response_name="ListingList",
    )
    @action
    async def Listings(self, request: Any, context: Any) -> Any:
        pb2 = _pb2()
        rows = await sync_to_async(shop_service.listings)()
        return pb2.ListingList(listings=[pb2.ListingSummary(**row) for row in rows])

    @grpc_action(
        request=[
            {"name": "key", "type": "string"},
            {"name": "limit", "type": "int32"},
            {"name": "offset", "type": "int32"},
        ],
        request_name="ListingRequest",
        response=[
            {"name": "key", "type": "string"},
            {"name": "name", "type": "string"},
            {"name": "description", "type": "string"},
            {"name": "page", "type": ProductPage},
        ],
        response_name="ListingResult",
    )
    @action
    async def Listing(self, request: Any, context: Any) -> Any:
        try:
            row = await sync_to_async(shop_service.listing)(
                request.key, limit=request.limit or None, offset=request.offset
            )
        except ShopNotFound as error:
            raise _missing(error) from None
        return _pb2().ListingResult(
            key=row["key"], name=row["name"], description=row["description"], page=_page(row)
        )

    @grpc_action(request=[], response=[{"name": "cart", "type": Cart}], response_name="CartResult")
    @action
    async def GetCart(self, request: Any, context: Any) -> Any:
        user = require_caller(await grpc_caller(context))
        return _pb2().CartResult(cart=_cart(await sync_to_async(shop_service.cart)(user)))

    @grpc_action(
        request=[
            {"name": "product", "type": "string"},
            {"name": "variant", "type": "string"},
            {"name": "offer", "type": "string"},
            {"name": "quantity", "type": "int32"},
        ],
        request_name="AddToCartRequest",
        response=[{"name": "cart", "type": Cart}],
        response_name="AddToCartResult",
    )
    @action
    async def AddToCart(self, request: Any, context: Any) -> Any:
        user = require_caller(await grpc_caller(context))
        try:
            row = await sync_to_async(shop_service.add_to_cart)(
                user,
                request.product,
                variant_id=request.variant or None,
                offer_id=request.offer or None,
                quantity=request.quantity or 1,
            )
        except ShopNotFound as error:
            raise _missing(error) from None
        except ShopRefused as error:
            raise _refused(error) from None
        return _pb2().AddToCartResult(cart=_cart(row))

    @grpc_action(
        request=[],
        response=[{"name": "sellers", "cardinality": "repeated", "type": Seller}],
        response_name="SellerList",
    )
    @action
    async def Sellers(self, request: Any, context: Any) -> Any:
        rows = await sync_to_async(shop_service.sellers)()
        return _pb2().SellerList(sellers=[_seller(row) for row in rows])

    @grpc_action(
        request=[{"name": "slug", "type": "string"}],
        request_name="SellerRequest",
        response=[{"name": "seller", "type": Seller}],
        response_name="SellerResult",
    )
    @action
    async def Seller(self, request: Any, context: Any) -> Any:
        try:
            row = await sync_to_async(shop_service.seller)(request.slug)
        except ShopNotFound as error:
            raise _missing(error) from None
        return _pb2().SellerResult(seller=_seller(row))

    @grpc_action(
        request=[
            {"name": "limit", "type": "int32"},
            {"name": "offset", "type": "int32"},
        ],
        request_name="OrdersRequest",
        response=[
            {"name": "orders", "cardinality": "repeated", "type": Order},
            {"name": "total", "type": "int32"},
        ],
        response_name="OrderList",
    )
    @action
    async def Orders(self, request: Any, context: Any) -> Any:
        user = require_caller(await grpc_caller(context))
        page = await sync_to_async(shop_service.orders)(
            user, limit=request.limit or None, offset=request.offset
        )
        return _pb2().OrderList(orders=[_order(row) for row in page["items"]], total=page["total"])

    @grpc_action(
        request=[{"name": "number", "type": "string"}],
        request_name="OrderRequest",
        response=[{"name": "order", "type": Order}],
        response_name="OrderResult",
    )
    @action
    async def Order(self, request: Any, context: Any) -> Any:
        user = require_caller(await grpc_caller(context))
        try:
            row = await sync_to_async(shop_service.order)(user, request.number)
        except ShopNotFound as error:
            raise _missing(error) from None
        return _pb2().OrderResult(order=_order(row))

    @grpc_action(
        request=[{"name": "number", "type": "string"}],
        request_name="InvoiceRequest",
        response=[{"name": "invoice", "type": Invoice}],
        response_name="InvoiceResult",
    )
    @action
    async def Invoice(self, request: Any, context: Any) -> Any:
        user = require_caller(await grpc_caller(context))
        try:
            row = await sync_to_async(shop_service.invoice)(user, request.number)
        except ShopNotFound as error:
            raise _missing(error) from None
        return _pb2().InvoiceResult(invoice=_invoice(row))


GRPC_SERVICES = [ShopService]
