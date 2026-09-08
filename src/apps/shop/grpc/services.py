"""The storefront over gRPC.

Catalogue calls are public. Basket calls derive their user from invocation
metadata, so a client can never ask for another customer's cart.
"""

from __future__ import annotations

import json
from decimal import Decimal, InvalidOperation
from typing import Any

from asgiref.sync import sync_to_async
from django_socio_grpc import generics
from django_socio_grpc.decorators import grpc_action

from apps.shop.attributes import parse_filters
from apps.shop.grpc.serializers import (
    Address,
    Brand,
    Cart,
    CategoryDetail,
    CategorySummary,
    Collection,
    CollectionSummary,
    CouponPreview,
    Invoice,
    ListingSummary,
    Order,
    ProductDetail,
    ProductPage,
    ProductSummary,
    Review,
    ReviewPage,
    Seller,
    ShippingMethod,
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


def _money(value: str, field: str) -> Decimal | None:
    """One money-shaped filter bound, as a string because protobuf has no decimal.

    An empty string is "no bound" rather than zero -- a `max_price` of nothing
    must not mean "free only". Anything else that is not a number is refused
    here, which is what the HTTP door does with the same value.
    """
    if not value:
        return None
    try:
        return Decimal(value)
    except InvalidOperation:
        raise ApiError(
            f"{field} is not a number.", status=400, title=ResponseTitle.VALIDATION_ERROR
        ) from None


def _attribute_filters(pairs: Any) -> dict[str, str]:
    """The repeated ``code:value`` entries, read the way the query string is."""
    return parse_filters(list(pairs))


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


def _card(row: dict[str, Any]) -> dict[str, Any]:
    """The fields a card carries, shared by ``ProductSummary`` and ``ProductDetail``.

    One dictionary rather than two constructions, because a summary and a detail
    that disagree about what ``in_stock`` means is exactly the drift the single
    service layer exists to prevent.
    """
    return dict(
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


def _product(row: dict[str, Any]) -> Any:
    return _pb2().ProductSummary(**_card(row))


def _seo_meta(row: dict[str, Any]) -> Any:
    return _pb2().SeoMeta(
        title=row["title"], description=row["description"], keywords=row["keywords"]
    )


def _breadcrumbs(rows: list[dict[str, Any]]) -> list[Any]:
    pb2 = _pb2()
    return [pb2.Breadcrumb(name=row["name"], slug=row["slug"]) for row in rows]


def _attribute(row: dict[str, Any]) -> Any:
    return _pb2().Attribute(
        code=row["code"],
        name=row["name"],
        type=row["type"],
        unit=row["unit"],
        choices=row["choices"],
        required=row["required"],
        is_variant=row["is_variant"],
        is_filterable=row["is_filterable"],
        help_text=row["help_text"],
    )


def _variant_attribute(row: dict[str, Any]) -> Any:
    """A variant axis, with the values a shopper can still pick."""
    pb2 = _pb2()
    return pb2.VariantAttribute(
        code=row["code"],
        name=row["name"],
        type=row["type"],
        unit=row["unit"],
        choices=row["choices"],
        required=row["required"],
        is_variant=row["is_variant"],
        is_filterable=row["is_filterable"],
        help_text=row["help_text"],
        values=[
            pb2.OptionValue(
                value=value["value"], in_stock=value["in_stock"], variants=value["variants"]
            )
            for value in row["values"]
        ],
    )


def _category_summary(row: dict[str, Any]) -> Any:
    """One category row. ``parent`` is a slug, and a root's is the empty string."""
    return _pb2().CategorySummary(
        id=row["id"],
        name=row["name"],
        slug=row["slug"],
        description=row["description"],
        image=row["image"],
        icon=row["icon"],
        parent=row["parent"] or "",
        product_count=row["product_count"] or 0,
    )


def _category_detail(row: dict[str, Any]) -> Any:
    return _pb2().CategoryDetail(
        id=row["id"],
        name=row["name"],
        slug=row["slug"],
        description=row["description"],
        image=row["image"],
        icon=row["icon"],
        parent=row["parent"] or "",
        product_count=row["product_count"] or 0,
        breadcrumbs=_breadcrumbs(row["breadcrumbs"]),
        children=[_category_summary(child) for child in row["children"]],
        attributes=[_attribute(attribute) for attribute in row["attributes"]],
        meta=_seo_meta(row["meta"]),
    )


def _brand(row: dict[str, Any]) -> Any:
    return _pb2().Brand(
        id=row["id"],
        name=row["name"],
        slug=row["slug"],
        description=row["description"],
        logo=row["logo"],
        website=row["website"],
    )


def _review(row: dict[str, Any]) -> Any:
    return _pb2().Review(
        id=row["id"],
        product=row.get("product") or "",
        author=row["author"],
        rating=row["rating"],
        title=row["title"],
        body=row["body"],
        status=row["status"],
        created_at=row["created_at"].isoformat(),
        updated_at=row["updated_at"].isoformat(),
    )


def _review_page(row: dict[str, Any]) -> Any:
    """A page of reviews. The rating is absent on "my reviews", which has no product."""
    rating_average = row.get("rating_average")
    return _pb2().ReviewPage(
        items=[_review(item) for item in row["items"]],
        total=row["total"],
        limit=row["limit"],
        offset=row["offset"],
        rating_average=str(rating_average) if rating_average is not None else "",
        rating_count=row.get("rating_count") or 0,
    )


def _product_detail(row: dict[str, Any]) -> Any:
    """A whole product page.

    ``dimensions_mm`` is absent when nobody filled any of it in, and each part
    of it is flattened to its own field: protobuf has no null, and a missing
    length and a zero length mean the same thing to a shipping estimate.
    """
    pb2 = _pb2()
    dimensions = row.get("dimensions_mm") or {}
    published_at = row.get("published_at")
    detail = pb2.ProductDetail(
        **_card(row),
        description=row["description"],
        sku=row["sku"],
        barcode=row["barcode"],
        tax_rate=str(row["tax_rate"]),
        status=row["status"],
        published_at=published_at.isoformat() if published_at else "",
        stock=row.get("stock") or 0,
        track_inventory=row["track_inventory"],
        allow_backorder=row["allow_backorder"],
        weight_grams=row.get("weight_grams") or 0,
        length_mm=dimensions.get("length") or 0,
        width_mm=dimensions.get("width") or 0,
        height_mm=dimensions.get("height") or 0,
        images=[
            pb2.ProductImage(
                url=image["url"],
                alt=image["alt"],
                caption=image["caption"],
                is_primary=image["is_primary"],
            )
            for image in row["images"]
        ],
        attributes=[
            pb2.ProductAttributeValue(
                code=value["code"],
                name=value["name"],
                type=value["type"],
                unit=value["unit"],
                value_json=json.dumps(value["value"]),
            )
            for value in row["attributes"]
        ],
        variant_attributes=[
            _variant_attribute(attribute) for attribute in row["variant_attributes"]
        ],
        variants=[
            pb2.Variant(
                id=variant["id"],
                sku=variant["sku"],
                label=variant["label"],
                options_json=json.dumps(variant["options"]),
                image=variant["image"],
                stock=variant["stock"],
                total_stock=variant["total_stock"],
                in_stock=variant["in_stock"],
                sellers=[_offer(offer) for offer in variant["sellers"]],
                seller_count=variant["seller_count"],
                price=_price(variant["price"]),
            )
            for variant in row["variants"]
        ],
        breadcrumbs=_breadcrumbs(row["breadcrumbs"]),
        meta=_seo_meta(row["meta"]),
        liked=row["liked"],
        offers=[_offer(offer) for offer in row["offers"]],
        seller_count=row["seller_count"],
    )
    # Left unset rather than nulled: nobody sells this directly, and this
    # caller has not reviewed it.
    if row.get("sold_by"):
        detail.sold_by.CopyFrom(_seller(row["sold_by"]))
    if row.get("own_review"):
        detail.own_review.CopyFrom(_review(row["own_review"]))
    return detail


def _collection(row: dict[str, Any]) -> Any:
    return _pb2().Collection(
        id=row["id"],
        name=row["name"],
        slug=row["slug"],
        description=row["description"],
        image=row["image"],
        products=[_product(product) for product in row["products"]],
    )


def _collection_summary(row: dict[str, Any]) -> Any:
    return _pb2().CollectionSummary(
        id=row["id"],
        name=row["name"],
        slug=row["slug"],
        description=row["description"],
        image=row["image"],
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
        carrier=row.get("carrier") or "",
        tracking_number=row.get("tracking_number") or "",
        tracking_url=row.get("tracking_url") or "",
        shipped_at=row["shipped_at"].isoformat() if row.get("shipped_at") else "",
        history=[
            pb2.OrderEvent(
                status=event["status"],
                status_label=event["status_label"],
                note=event["note"],
                at=event["at"].isoformat(),
            )
            for event in row.get("history", [])
        ],
    )


def _address(row: dict[str, Any]) -> Any:
    return _pb2().Address(
        id=row["id"],
        label=row["label"],
        full_name=row["full_name"],
        phone=row["phone"],
        country=row["country"],
        province=row["province"],
        city=row["city"],
        postal_code=row["postal_code"],
        line1=row["line1"],
        line2=row["line2"],
        is_default=row["is_default"],
        created_at=row["created_at"].isoformat(),
        updated_at=row["updated_at"].isoformat(),
    )


def _shipping_method(row: dict[str, Any]) -> Any:
    """One delivery option. An empty ``free_from`` is "never free", not zero."""
    free_from = row.get("free_from")
    return _pb2().ShippingMethod(
        id=row["id"],
        name=row["name"],
        description=row["description"],
        price=str(row["price"]),
        cost=str(row["cost"]),
        is_free=row["is_free"],
        free_from=str(free_from) if free_from is not None else "",
        min_days=row["min_days"],
        max_days=row["max_days"],
        currency=row["currency"],
    )


def _coupon_preview(row: dict[str, Any]) -> Any:
    return _pb2().CouponPreview(
        code=row["code"],
        is_valid=row["is_valid"],
        reason=row["reason"],
        discount=str(row["discount"]),
        subtotal=str(row["subtotal"]),
        total=str(row["total"]),
        currency=row["currency"],
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
        return _pb2().CategoryList(categories=[_category_summary(row) for row in flat])

    @grpc_action(
        request=[{"name": "slug", "type": "string"}],
        request_name="CategoryRequest",
        response=[{"name": "category", "type": CategoryDetail}],
        response_name="CategoryResult",
    )
    @action
    async def Category(self, request: Any, context: Any) -> Any:
        """One category, with its breadcrumbs, its children and its attribute schema."""
        try:
            row = await sync_to_async(shop_service.category)(request.slug)
        except ShopNotFound as error:
            raise _missing(error) from None
        return _pb2().CategoryResult(category=_category_detail(row))

    @grpc_action(
        request=[],
        response=[{"name": "brands", "cardinality": "repeated", "type": Brand}],
        response_name="BrandList",
    )
    @action
    async def Brands(self, request: Any, context: Any) -> Any:
        """Every brand this shop stocks."""
        rows = await sync_to_async(shop_service.brands)()
        return _pb2().BrandList(brands=[_brand(row) for row in rows])

    @grpc_action(
        request=[
            {"name": "search", "type": "string"},
            {"name": "category", "type": "string"},
            {"name": "brand", "type": "string"},
            {"name": "seller", "type": "string"},
            {"name": "tag", "type": "string"},
            {"name": "min_price", "type": "string"},
            {"name": "max_price", "type": "string"},
            {"name": "min_rating", "type": "int32"},
            {"name": "in_stock", "type": "bool"},
            {"name": "on_sale", "type": "bool"},
            {"name": "featured", "type": "bool"},
            {"name": "attributes", "cardinality": "repeated", "type": "string"},
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
        """Search, a category page, and every filter on it, in one call.

        The filters read the way they do over HTTP, with two transport
        differences protobuf forces. Money arrives as a string, for the reason
        it leaves as one, and an empty one means "no bound" rather than zero.
        The three booleans are unset-as-false, so `in_stock=false` cannot ask
        for "only what is sold out" -- neither can `?in_stock=false` over HTTP,
        which the service reads the same way.

        ``attributes`` is repeated and reads `code:value`, so a malformed entry
        is refused rather than silently widening the search.
        """
        try:
            page = await sync_to_async(shop_service.products)(
                search=request.search,
                category=request.category or None,
                brand=request.brand or None,
                seller=request.seller or None,
                tag=request.tag or None,
                min_price=_money(request.min_price, "min_price"),
                max_price=_money(request.max_price, "max_price"),
                min_rating=request.min_rating or None,
                in_stock=request.in_stock or None,
                on_sale=request.on_sale or None,
                featured=request.featured or None,
                attributes=_attribute_filters(request.attributes),
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
        response=[{"name": "product", "type": ProductDetail}],
        response_name="ProductResult",
    )
    @action
    async def Product(self, request: Any, context: Any) -> Any:
        """Everything on a product page: images, variants, specs, price and rating.

        The credential is optional and read the way the HTTP route reads it: an
        anonymous caller gets the page, and one that offered a token gets
        ``liked`` and ``own_review`` filled in rather than having to ask again.
        """
        user = await grpc_caller(context)
        try:
            row = await sync_to_async(shop_service.product)(request.slug, user)
        except ShopNotFound as error:
            raise _missing(error) from None
        return _pb2().ProductResult(product=_product_detail(row))

    @grpc_action(
        request=[
            {"name": "slug", "type": "string"},
            {"name": "limit", "type": "int32"},
        ],
        request_name="RelatedProductsRequest",
        response=[{"name": "products", "cardinality": "repeated", "type": ProductSummary}],
        response_name="RelatedProductsResult",
    )
    @action
    async def RelatedProducts(self, request: Any, context: Any) -> Any:
        """Other products in the same category."""
        try:
            rows = await sync_to_async(shop_service.related)(request.slug, limit=request.limit or 8)
        except ShopNotFound as error:
            raise _missing(error) from None
        return _pb2().RelatedProductsResult(products=[_product(row) for row in rows])

    @grpc_action(
        request=[{"name": "slug", "type": "string"}],
        request_name="RecordViewRequest",
        response=[
            {"name": "product", "type": "string"},
            {"name": "view_count", "type": "int32"},
        ],
        response_name="RecordViewResult",
    )
    @action
    async def RecordView(self, request: Any, context: Any) -> Any:
        """Count that somebody looked, and answer with the new total.

        Its own call rather than a side effect of reading the product, because a
        page served from a cache still wants to count the view.
        """
        try:
            count = await sync_to_async(shop_service.view)(request.slug)
        except ShopNotFound as error:
            raise _missing(error) from None
        return _pb2().RecordViewResult(product=request.slug, view_count=count)

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

    @grpc_action(
        request=[],
        response=[{"name": "collections", "cardinality": "repeated", "type": CollectionSummary}],
        response_name="CollectionList",
    )
    @action
    async def Collections(self, request: Any, context: Any) -> Any:
        """The lists somebody arranged by hand, without their products.

        The summary rather than the whole thing, because a page listing twelve
        collections does not want twelve pages of products with it.
        """
        rows = await sync_to_async(shop_service.collections)()
        return _pb2().CollectionList(collections=[_collection_summary(row) for row in rows])

    @grpc_action(
        request=[
            {"name": "slug", "type": "string"},
            {"name": "limit", "type": "int32"},
        ],
        request_name="CollectionRequest",
        response=[{"name": "collection", "type": Collection}],
        response_name="CollectionResult",
    )
    @action
    async def Collection(self, request: Any, context: Any) -> Any:
        """One curated collection, with its products in the order they were arranged."""
        try:
            row = await sync_to_async(shop_service.collection)(
                request.slug, limit=request.limit or None
            )
        except ShopNotFound as error:
            raise _missing(error) from None
        return _pb2().CollectionResult(collection=_collection(row))

    @grpc_action(
        request=[
            {"name": "slug", "type": "string"},
            {"name": "limit", "type": "int32"},
            {"name": "offset", "type": "int32"},
        ],
        request_name="ReviewsRequest",
        response=[{"name": "page", "type": ReviewPage}],
        response_name="ReviewsResult",
    )
    @action
    async def Reviews(self, request: Any, context: Any) -> Any:
        """A product's published reviews, newest first, with the rating they add up to.

        A review waiting for a moderator is not here. Its author sees it on
        ``MyReviews``, which is the one place it appears.
        """
        try:
            row = await sync_to_async(shop_service.reviews)(
                request.slug, limit=request.limit or None, offset=request.offset
            )
        except ShopNotFound as error:
            raise _missing(error) from None
        return _pb2().ReviewsResult(page=_review_page(row))

    @grpc_action(
        request=[
            {"name": "slug", "type": "string"},
            {"name": "rating", "type": "int32"},
            {"name": "title", "type": "string"},
            {"name": "body", "type": "string"},
        ],
        request_name="ReviewProductRequest",
        response=[{"name": "review", "type": Review}],
        response_name="ReviewProductResult",
    )
    @action
    async def ReviewProduct(self, request: Any, context: Any) -> Any:
        """Write this caller's review of a product, or replace the one they wrote.

        One review per account, so writing a second edits the first -- and an
        edited review goes back through moderation where moderation is on.
        """
        user = require_caller(await grpc_caller(context))
        try:
            row = await sync_to_async(shop_service.review_product)(
                user,
                request.slug,
                rating=request.rating,
                title=request.title,
                body=request.body,
            )
        except ShopNotFound as error:
            raise _missing(error) from None
        except ShopRefused as error:
            raise _refused(error) from None
        return _pb2().ReviewProductResult(review=_review(row))

    @grpc_action(
        request=[{"name": "slug", "type": "string"}],
        request_name="DeleteReviewRequest",
        response=[
            {"name": "product", "type": "string"},
            {"name": "rating_average", "type": "string"},
            {"name": "rating_count", "type": "int32"},
        ],
        response_name="DeleteReviewResult",
    )
    @action
    async def DeleteReview(self, request: Any, context: Any) -> Any:
        """Take back this caller's own review. Somebody else's is a 404, not a 403."""
        user = require_caller(await grpc_caller(context))
        try:
            row = await sync_to_async(shop_service.delete_review)(user, request.slug)
        except ShopNotFound as error:
            raise _missing(error) from None
        return _pb2().DeleteReviewResult(
            product=row["product"],
            rating_average=str(row["rating_average"]),
            rating_count=row["rating_count"],
        )

    @grpc_action(
        request=[
            {"name": "limit", "type": "int32"},
            {"name": "offset", "type": "int32"},
        ],
        request_name="MyReviewsRequest",
        response=[{"name": "page", "type": ReviewPage}],
        response_name="MyReviewsResult",
    )
    @action
    async def MyReviews(self, request: Any, context: Any) -> Any:
        """Everything this caller has written, including what is still in the queue."""
        user = require_caller(await grpc_caller(context))
        row = await sync_to_async(shop_service.my_reviews)(
            user, limit=request.limit or None, offset=request.offset
        )
        return _pb2().MyReviewsResult(page=_review_page(row))

    @grpc_action(
        request=[{"name": "slug", "type": "string"}],
        request_name="LikeProductRequest",
        response=[
            {"name": "product", "type": "string"},
            {"name": "liked", "type": "bool"},
            {"name": "like_count", "type": "int32"},
        ],
        response_name="LikeResult",
    )
    @action
    async def LikeProduct(self, request: Any, context: Any) -> Any:
        """Mark a product. Idempotent: liking twice is a no-op, not an error."""
        user = require_caller(await grpc_caller(context))
        try:
            row = await sync_to_async(shop_service.like)(user, request.slug)
        except ShopNotFound as error:
            raise _missing(error) from None
        return _pb2().LikeResult(
            product=row["product"], liked=row["liked"], like_count=row["like_count"]
        )

    @grpc_action(
        request=[{"name": "slug", "type": "string"}],
        request_name="UnlikeProductRequest",
        response=[
            {"name": "product", "type": "string"},
            {"name": "liked", "type": "bool"},
            {"name": "like_count", "type": "int32"},
        ],
        response_name="UnlikeResult",
    )
    @action
    async def UnlikeProduct(self, request: Any, context: Any) -> Any:
        """Unmark it. Also idempotent, for the same reason."""
        user = require_caller(await grpc_caller(context))
        try:
            row = await sync_to_async(shop_service.unlike)(user, request.slug)
        except ShopNotFound as error:
            raise _missing(error) from None
        return _pb2().UnlikeResult(
            product=row["product"], liked=row["liked"], like_count=row["like_count"]
        )

    @grpc_action(
        request=[
            {"name": "limit", "type": "int32"},
            {"name": "offset", "type": "int32"},
        ],
        request_name="FavouritesRequest",
        response=[{"name": "page", "type": ProductPage}],
        response_name="FavouritesResult",
    )
    @action
    async def Favourites(self, request: Any, context: Any) -> Any:
        """Everything this caller has liked, most recently first."""
        user = require_caller(await grpc_caller(context))
        page = await sync_to_async(shop_service.liked)(
            user, limit=request.limit or None, offset=request.offset
        )
        return _pb2().FavouritesResult(page=_page(page))

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
        request=[
            {"name": "item_id", "type": "string"},
            {"name": "quantity", "type": "int32"},
        ],
        request_name="SetCartQuantityRequest",
        response=[{"name": "cart", "type": Cart}],
        response_name="SetCartQuantityResult",
    )
    @action
    async def SetCartQuantity(self, request: Any, context: Any) -> Any:
        """Set a line to an absolute quantity, rather than adding to it.

        A quantity of zero removes the line, which is what a quantity box set to
        nothing means to a shopper.
        """
        user = require_caller(await grpc_caller(context))
        try:
            row = await sync_to_async(shop_service.set_cart_quantity)(
                user, request.item_id, request.quantity
            )
        except ShopNotFound as error:
            raise _missing(error) from None
        except ShopRefused as error:
            raise _refused(error) from None
        return _pb2().SetCartQuantityResult(cart=_cart(row))

    @grpc_action(
        request=[{"name": "item_id", "type": "string"}],
        request_name="RemoveFromCartRequest",
        response=[{"name": "cart", "type": Cart}],
        response_name="RemoveFromCartResult",
    )
    @action
    async def RemoveFromCart(self, request: Any, context: Any) -> Any:
        """Take one line out, and answer with the basket that is left."""
        user = require_caller(await grpc_caller(context))
        try:
            row = await sync_to_async(shop_service.remove_from_cart)(user, request.item_id)
        except ShopNotFound as error:
            raise _missing(error) from None
        return _pb2().RemoveFromCartResult(cart=_cart(row))

    @grpc_action(
        request=[],
        response=[{"name": "cart", "type": Cart}],
        response_name="ClearCartResult",
    )
    @action
    async def ClearCart(self, request: Any, context: Any) -> Any:
        """Empty the basket. Answers with the empty basket rather than a bare flag."""
        user = require_caller(await grpc_caller(context))
        row = await sync_to_async(shop_service.clear_cart)(user)
        return _pb2().ClearCartResult(cart=_cart(row))

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

    # The two actions that answer with an `Order` message come before the
    # `Order` *action*, and have to. Inside a class body a name means whatever
    # was last bound to it, so after `async def Order` the name `Order` is that
    # action rather than the serializer it shadows -- and the proto generator
    # fails on the field type with an error naming neither.
    @grpc_action(
        request=[
            {"name": "address_id", "type": "string"},
            {"name": "shipping_method_id", "type": "string"},
            {"name": "coupon", "type": "string"},
            {"name": "note", "type": "string"},
        ],
        request_name="CheckoutRequest",
        response=[{"name": "order", "type": Order}],
        response_name="CheckoutResult",
    )
    @action
    async def Checkout(self, request: Any, context: Any) -> Any:
        """Turn the caller's basket into an order, an invoice and a payment to settle.

        Stock moves here rather than when the money arrives, so two shoppers on
        a checkout page for the last one in stock cannot both succeed.
        """
        user = require_caller(await grpc_caller(context))
        try:
            order = await sync_to_async(shop_service.checkout)(
                user,
                address_id=request.address_id,
                shipping_method_id=request.shipping_method_id,
                coupon_code=request.coupon,
                note=request.note,
            )
            row = await sync_to_async(shop_service.order)(user, order.number)
        except ShopNotFound as error:
            raise _missing(error) from None
        except ShopRefused as error:
            raise _refused(error) from None
        return _pb2().CheckoutResult(order=_order(row))

    @grpc_action(
        request=[{"name": "number", "type": "string"}],
        request_name="CancelOrderRequest",
        response=[{"name": "order", "type": Order}],
        response_name="CancelOrderResult",
    )
    @action
    async def CancelOrder(self, request: Any, context: Any) -> Any:
        """Cancel an unpaid order and put its stock back, exactly once."""
        user = require_caller(await grpc_caller(context))
        try:
            order = await sync_to_async(shop_service.cancel_order)(user, request.number)
            row = await sync_to_async(shop_service.order)(user, order.number)
        except ShopNotFound as error:
            raise _missing(error) from None
        except ShopRefused as error:
            raise _refused(error) from None
        return _pb2().CancelOrderResult(order=_order(row))

    @grpc_action(
        request=[
            {"name": "number", "type": "string"},
            {"name": "reference", "type": "string"},
        ],
        request_name="ConfirmPaymentRequest",
        response=[{"name": "order", "type": Order}],
        response_name="ConfirmPaymentResult",
    )
    @action
    async def ConfirmPayment(self, request: Any, context: Any) -> Any:
        """The seam a payment provider's callback is pointed at.

        This starter wires up no gateway -- payments are created against the
        `manual` provider and settled by somebody in the admin looking at a bank
        statement. This is what a project points a real callback at once it has
        one, and it settles the order exactly the way the admin does.
        """
        user = require_caller(await grpc_caller(context))
        try:
            order = await sync_to_async(shop_service.confirm_payment)(
                user, request.number, reference=request.reference
            )
            row = await sync_to_async(shop_service.order)(user, order.number)
        except ShopNotFound as error:
            raise _missing(error) from None
        except ShopRefused as error:
            raise _refused(error) from None
        return _pb2().ConfirmPaymentResult(order=_order(row))

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
        request=[],
        response=[{"name": "addresses", "cardinality": "repeated", "type": Address}],
        response_name="AddressList",
    )
    @action
    async def Addresses(self, request: Any, context: Any) -> Any:
        """The caller's address book, the default one first."""
        user = require_caller(await grpc_caller(context))
        rows = await sync_to_async(shop_service.addresses)(user)
        return _pb2().AddressList(addresses=[_address(row) for row in rows])

    @grpc_action(
        request=[
            {"name": "label", "type": "string"},
            {"name": "full_name", "type": "string"},
            {"name": "phone", "type": "string"},
            {"name": "country", "type": "string"},
            {"name": "province", "type": "string"},
            {"name": "city", "type": "string"},
            {"name": "postal_code", "type": "string"},
            {"name": "line1", "type": "string"},
            {"name": "line2", "type": "string"},
            {"name": "is_default", "type": "bool"},
        ],
        request_name="AddAddressRequest",
        response=[{"name": "address", "type": Address}],
        response_name="AddressResult",
    )
    @action
    async def AddAddress(self, request: Any, context: Any) -> Any:
        """Save somewhere to send an order to. The first one saved becomes the default."""
        user = require_caller(await grpc_caller(context))
        try:
            row = await sync_to_async(shop_service.add_address)(
                user,
                label=request.label or "Home",
                full_name=request.full_name,
                phone=request.phone,
                country=request.country,
                province=request.province,
                city=request.city,
                postal_code=request.postal_code,
                line1=request.line1,
                line2=request.line2,
                is_default=request.is_default,
            )
        except ShopRefused as error:
            raise _refused(error) from None
        return _pb2().AddressResult(address=_address(row))

    @grpc_action(
        request=[
            {"name": "address_id", "type": "string"},
            {"name": "label", "type": "string"},
            {"name": "full_name", "type": "string"},
            {"name": "phone", "type": "string"},
            {"name": "country", "type": "string"},
            {"name": "province", "type": "string"},
            {"name": "city", "type": "string"},
            {"name": "postal_code", "type": "string"},
            {"name": "line1", "type": "string"},
            {"name": "line2", "type": "string"},
        ],
        request_name="UpdateAddressRequest",
        response=[{"name": "address", "type": Address}],
        response_name="UpdateAddressResult",
    )
    @action
    async def UpdateAddress(self, request: Any, context: Any) -> Any:
        """Change part of a saved address, leaving out whatever was not sent.

        A field left empty is left alone, which is the reading the service
        already commits to: "a transport that omits ``line2`` and one that sends
        it empty must not mean two different things". Protobuf has no unset for
        a scalar, so empty is the only way to say "not sent" -- with the
        consequence that this call cannot *clear* an optional field. Blanking a
        ``line2`` is the HTTP door's PATCH, which can tell the two apart.

        ``is_default`` is deliberately not here: choosing the default is
        ``SetDefaultAddress``, because a bool that cannot be distinguished from
        unset would silently unset it on every other edit.
        """
        user = require_caller(await grpc_caller(context))
        sent = {
            field: getattr(request, field) or None
            for field in (
                "label",
                "full_name",
                "phone",
                "country",
                "province",
                "city",
                "postal_code",
                "line1",
                "line2",
            )
        }
        try:
            row = await sync_to_async(shop_service.update_address)(user, request.address_id, **sent)
        except ShopNotFound as error:
            raise _missing(error) from None
        except ShopRefused as error:
            raise _refused(error) from None
        return _pb2().UpdateAddressResult(address=_address(row))

    @grpc_action(
        request=[{"name": "address_id", "type": "string"}],
        request_name="AddressRequest",
        response=[{"name": "address", "type": Address}],
        response_name="DefaultAddressResult",
    )
    @action
    async def SetDefaultAddress(self, request: Any, context: Any) -> Any:
        """Choosing one unchooses the last, so "the default" always means one address."""
        user = require_caller(await grpc_caller(context))
        try:
            row = await sync_to_async(shop_service.set_default_address)(user, request.address_id)
        except ShopNotFound as error:
            raise _missing(error) from None
        except ShopRefused as error:
            raise _refused(error) from None
        return _pb2().DefaultAddressResult(address=_address(row))

    @grpc_action(
        request=[{"name": "address_id", "type": "string"}],
        request_name="RemoveAddressRequest",
        response=[{"name": "deleted", "type": "bool"}],
        response_name="AddressRemoved",
    )
    @action
    async def RemoveAddress(self, request: Any, context: Any) -> Any:
        user = require_caller(await grpc_caller(context))
        try:
            await sync_to_async(shop_service.remove_address)(user, request.address_id)
        except ShopNotFound as error:
            raise _missing(error) from None
        return _pb2().AddressRemoved(deleted=True)

    # `GetAddress` rather than `Address` for the reason `GetCart` is not `Cart`:
    # an action named after the serializer it answers with shadows it for every
    # action declared below.
    @grpc_action(
        request=[{"name": "address_id", "type": "string"}],
        request_name="GetAddressRequest",
        response=[{"name": "address", "type": Address}],
        response_name="GetAddressResult",
    )
    @action
    async def GetAddress(self, request: Any, context: Any) -> Any:
        """One of the caller's addresses. Somebody else's does not exist."""
        user = require_caller(await grpc_caller(context))
        try:
            row = await sync_to_async(shop_service.address)(user, request.address_id)
        except ShopNotFound as error:
            raise _missing(error) from None
        return _pb2().GetAddressResult(address=_address(row))

    @grpc_action(
        request=[],
        response=[{"name": "methods", "cardinality": "repeated", "type": ShippingMethod}],
        response_name="ShippingMethodList",
    )
    @action
    async def ShippingMethods(self, request: Any, context: Any) -> Any:
        """Every delivery option, costed for the caller's basket where there is one.

        The account is optional here for the reason it is optional over HTTP: a
        shopper comparing delivery options has not necessarily signed in, and a
        signed-in one gets "free over 50" answered against what is actually in
        their basket.
        """
        user = await grpc_caller(context)
        rows = await sync_to_async(shop_service.shipping_methods)(user)
        return _pb2().ShippingMethodList(methods=[_shipping_method(row) for row in rows])

    @grpc_action(
        request=[{"name": "code", "type": "string"}],
        request_name="CouponPreviewRequest",
        response=[{"name": "preview", "type": CouponPreview}],
        response_name="CouponPreviewResult",
    )
    @action
    async def PreviewCoupon(self, request: Any, context: Any) -> Any:
        """What a code would take off this basket, without committing to it."""
        user = require_caller(await grpc_caller(context))
        try:
            row = await sync_to_async(shop_service.preview_coupon)(user, request.code)
        except ShopRefused as error:
            raise _refused(error) from None
        return _pb2().CouponPreviewResult(preview=_coupon_preview(row))

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
