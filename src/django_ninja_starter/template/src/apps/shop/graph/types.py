"""GraphQL types for the catalogue, the basket and what shoppers say about both.

The same shapes the REST schemas publish, in Strawberry's vocabulary, built from
the same payload dictionaries -- so a field cannot exist on one transport and be
missing from the other by accident.

Two things travel as the ``JSON`` scalar, and both for the same reason. A
product attribute's ``value`` has a shape decided by its ``type``, which the
same object carries; a variant's ``options`` is a map whose keys are whatever
the category declared. A union of every canonical shape would have to be edited
the first time a shop invents a new kind of attribute, and would make every
generated client worse in the meantime.
"""

from datetime import datetime
from decimal import Decimal
from typing import Any

import strawberry
from strawberry.scalars import JSON


@strawberry.type
class BreadcrumbType:
    name: str
    slug: str


@strawberry.type
class BrandType:
    id: str
    name: str
    slug: str
    description: str
    logo: str
    website: str


@strawberry.type
class AttributeType:
    """One thing every product in a category answers, and how."""

    code: str
    name: str
    type: str
    unit: str
    choices: list[str]
    required: bool
    is_variant: bool
    is_filterable: bool
    help_text: str


@strawberry.type
class OptionValueType:
    """One value of one variant axis, and whether a shopper can still press it."""

    value: str
    in_stock: bool
    variants: list[str]


@strawberry.type
class VariantAttributeType(AttributeType):
    """A variant axis on a product page: the attribute, plus which values are left."""

    values: list[OptionValueType]


@strawberry.type
class CategorySummaryType:
    id: str
    name: str
    slug: str
    description: str
    image: str
    icon: str
    parent: str | None
    product_count: int | None


@strawberry.type
class CategoryNodeType(CategorySummaryType):
    children: list["CategoryNodeType"]


@strawberry.type
class SeoType:
    title: str
    description: str
    keywords: list[str]


@strawberry.type
class CategoryType(CategorySummaryType):
    breadcrumbs: list[BreadcrumbType]
    children: list[CategorySummaryType]
    attributes: list[AttributeType]
    meta: SeoType


@strawberry.type
class AppliedDiscountType:
    """The campaign a price is under. ``ends_at`` is what a countdown counts to."""

    id: str
    name: str
    kind: str
    value: Decimal
    amount_off: Decimal
    percent_off: int
    ends_at: datetime | None


@strawberry.type
class PriceType:
    amount: Decimal
    base_amount: Decimal
    currency: str
    is_discounted: bool
    discount: AppliedDiscountType | None


@strawberry.type
class ImageType:
    url: str
    alt: str
    caption: str
    is_primary: bool


@strawberry.type
class ProductAttributeType:
    code: str
    name: str
    type: str
    unit: str
    value: JSON | None


@strawberry.type
class DimensionsType:
    length: int | None
    width: int | None
    height: int | None


@strawberry.type
class ReviewType:
    """One review. The author is a display name, never an address."""

    id: str
    product: str | None
    author: str
    rating: int
    title: str
    body: str
    status: str
    created_at: datetime
    updated_at: datetime


@strawberry.type
class SellerType:
    """One seller, as a "sold by" line names them."""

    id: str
    name: str
    slug: str
    description: str
    logo: str
    city: str


@strawberry.type
class SellerDetailType(SellerType):
    product_count: int


@strawberry.type
class OfferType:
    """One seller's offer of one thing, priced now."""

    id: str
    seller: SellerType
    variant: str | None
    sku: str
    condition: str
    lead_time_days: int
    stock: int
    in_stock: bool
    price: PriceType


@strawberry.type
class VariantType:
    """One buyable version of a product, and everybody holding it.

    ``stock`` is the shop's own shelf; ``total_stock`` counts every seller's.
    Declared after ``OfferType`` because ``sellers`` names it.
    """

    id: str
    sku: str
    label: str
    options: JSON
    image: str
    stock: int
    total_stock: int
    in_stock: bool
    sellers: list[OfferType]
    seller_count: int
    price: PriceType


@strawberry.type
class ProductSummaryType:
    """A card: what a listing, a search result and a related row need."""

    id: str
    slug: str
    name: str
    subtitle: str
    summary: str
    image: str
    brand: str | None
    brand_slug: str | None
    seller: str | None
    seller_slug: str | None
    category: str
    category_name: str
    price: PriceType
    compare_at_price: Decimal | None
    in_stock: bool
    has_variants: bool
    is_featured: bool
    rating_average: Decimal
    rating_count: int
    like_count: int
    sales_count: int
    tags: list[str]
    created_at: datetime


@strawberry.type
class ProductType(ProductSummaryType):
    description: str
    sku: str
    barcode: str
    tax_rate: Decimal
    status: str
    published_at: datetime | None
    stock: int | None
    track_inventory: bool
    allow_backorder: bool
    weight_grams: int | None
    dimensions_mm: DimensionsType | None
    images: list[ImageType]
    attributes: list[ProductAttributeType]
    variant_attributes: list[VariantAttributeType]
    variants: list[VariantType]
    breadcrumbs: list[BreadcrumbType]
    meta: SeoType
    liked: bool
    own_review: ReviewType | None
    # Who sells it. `sold_by` is the seller whose price this page quotes, and
    # `offers` is everybody else who could fill the order.
    sold_by: SellerType | None
    offers: list[OfferType]
    seller_count: int


@strawberry.type
class ProductPageType:
    """A page of products and the total, so a client can say "1-24 of 312"."""

    items: list[ProductSummaryType]
    total: int
    limit: int
    offset: int


@strawberry.type
class ListingType:
    key: str
    name: str
    description: str
    items: list[ProductSummaryType]
    total: int
    limit: int
    offset: int


@strawberry.type
class ListingSummaryType:
    key: str
    name: str
    description: str


@strawberry.type
class CollectionType:
    id: str
    name: str
    slug: str
    description: str
    image: str
    products: list[ProductSummaryType]


@strawberry.type
class ReviewPageType:
    items: list[ReviewType]
    total: int
    limit: int
    offset: int
    rating_average: Decimal
    rating_count: int


@strawberry.type
class LikeType:
    product: str
    liked: bool
    like_count: int


@strawberry.type
class ReviewRemovedType:
    product: str
    rating_average: Decimal
    rating_count: int


@strawberry.type
class ProductViewType:
    product: str
    view_count: int


@strawberry.type
class OrderLineType:
    product: str | None
    name: str
    seller: str | None
    sku: str
    quantity: int
    unit_price: Decimal
    tax_rate: Decimal
    line_total: Decimal


@strawberry.type
class OrderType:
    """One order, read entirely off its own snapshot.

    The delivery address is deliberately not here. It is a flat object on the
    payload and would have to become a type with eight nullable fields to say
    nothing more than the REST document already does; a client that needs to
    print it reads the order over HTTP.
    """

    number: str
    status: str
    status_label: str
    currency: str
    placed_at: datetime
    items: list[OrderLineType]
    subtotal: Decimal
    coupon: str | None
    coupon_discount: Decimal
    shipping_method: str | None
    shipping_total: Decimal
    tax_total: Decimal
    total: Decimal
    note: str
    payment_status: str | None
    invoice: str | None
    carrier: str
    tracking_number: str
    tracking_url: str
    shipped_at: datetime | None
    history: list["OrderEventType"]


@strawberry.type
class OrderEventType:
    """One step of an order's history, as the shopper is allowed to see it."""

    status: str
    status_label: str
    note: str
    at: datetime


@strawberry.type
class OrderPageType:
    items: list[OrderType]
    total: int
    limit: int
    offset: int


@strawberry.type
class InvoiceType:
    """The document, with the order it demands payment for nested whole."""

    number: str
    issued_at: datetime
    due_at: datetime | None
    notes: str
    order: OrderType


@strawberry.type
class AddressType:
    """One saved delivery address. The account it belongs to is never on it."""

    id: str
    label: str
    full_name: str
    phone: str
    country: str
    province: str
    city: str
    postal_code: str
    line1: str
    line2: str
    is_default: bool
    created_at: datetime
    updated_at: datetime


@strawberry.input
class AddressInput:
    """A new address, or an edit. Everything but the six a parcel needs has a default."""

    full_name: str
    phone: str
    country: str
    city: str
    postal_code: str
    line1: str
    label: str = "Home"
    province: str = ""
    line2: str = ""
    is_default: bool = False


@strawberry.type
class AddressRemovedType:
    deleted: bool


@strawberry.type
class ShippingMethodType:
    """One delivery option, costed for the basket that asked for it."""

    id: str
    name: str
    description: str
    price: Decimal
    cost: Decimal
    is_free: bool
    free_from: Decimal | None
    min_days: int
    max_days: int
    currency: str


@strawberry.type
class CouponPreviewType:
    """What a code is worth on this basket -- or, in the same shape, why it is not."""

    code: str
    is_valid: bool
    reason: str
    discount: Decimal
    subtotal: Decimal
    total: Decimal
    currency: str


@strawberry.type
class CartItemType:
    id: str
    product: str
    name: str
    image: str
    variant: str | None
    variant_label: str | None
    offer: str | None
    seller: str | None
    quantity: int
    unit_price: PriceType
    line_total: Decimal
    in_stock: bool


@strawberry.type
class CartType:
    """The basket, priced now rather than when things were added to it."""

    id: str
    currency: str
    items: list[CartItemType]
    item_count: int
    unit_count: int
    subtotal: Decimal
    discount_total: Decimal
    total: Decimal
    updated_at: datetime


def breadcrumb_types(rows: list[dict[str, Any]]) -> list[BreadcrumbType]:
    return [BreadcrumbType(name=row["name"], slug=row["slug"]) for row in rows]


def seller_type(row: dict[str, Any]) -> SellerType:
    return SellerType(
        id=row["id"],
        name=row["name"],
        slug=row["slug"],
        description=row["description"],
        logo=row["logo"],
        city=row["city"],
    )


def seller_detail_type(row: dict[str, Any]) -> SellerDetailType:
    return SellerDetailType(
        **{field: row[field] for field in ("id", "name", "slug", "description", "logo", "city")},
        product_count=row["product_count"],
    )


def offer_type(row: dict[str, Any]) -> OfferType:
    return OfferType(
        id=row["id"],
        seller=seller_type(row["seller"]),
        variant=row.get("variant"),
        sku=row["sku"],
        condition=row["condition"],
        lead_time_days=row["lead_time_days"],
        stock=row["stock"],
        in_stock=row["in_stock"],
        price=price_type(row["price"]),
    )


def brand_type(row: dict[str, Any]) -> BrandType:
    return BrandType(
        id=row["id"],
        name=row["name"],
        slug=row["slug"],
        description=row["description"],
        logo=row["logo"],
        website=row["website"],
    )


def attribute_type(row: dict[str, Any]) -> AttributeType:
    return AttributeType(
        code=row["code"],
        name=row["name"],
        type=row["type"],
        unit=row["unit"],
        choices=list(row["choices"]),
        required=row["required"],
        is_variant=row["is_variant"],
        is_filterable=row["is_filterable"],
        help_text=row["help_text"],
    )


def variant_attribute_type(row: dict[str, Any]) -> VariantAttributeType:
    """A variant axis, with the values a shopper can still pick."""
    return VariantAttributeType(
        code=row["code"],
        name=row["name"],
        type=row["type"],
        unit=row["unit"],
        choices=list(row["choices"]),
        required=row["required"],
        is_variant=row["is_variant"],
        is_filterable=row["is_filterable"],
        help_text=row["help_text"],
        values=[
            OptionValueType(
                value=value["value"],
                in_stock=value["in_stock"],
                variants=list(value["variants"]),
            )
            for value in row.get("values", [])
        ],
    )


def _category_fields(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": row["id"],
        "name": row["name"],
        "slug": row["slug"],
        "description": row["description"],
        "image": row["image"],
        "icon": row["icon"],
        "parent": row.get("parent"),
        "product_count": row.get("product_count"),
    }


def category_summary_type(row: dict[str, Any]) -> CategorySummaryType:
    return CategorySummaryType(**_category_fields(row))


def category_node_type(row: dict[str, Any]) -> CategoryNodeType:
    return CategoryNodeType(
        **_category_fields(row),
        children=[category_node_type(child) for child in row.get("children", [])],
    )


def seo_type(row: dict[str, Any]) -> SeoType:
    return SeoType(
        title=row["title"], description=row["description"], keywords=list(row["keywords"])
    )


def category_type(row: dict[str, Any]) -> CategoryType:
    return CategoryType(
        **_category_fields(row),
        breadcrumbs=breadcrumb_types(row.get("breadcrumbs", [])),
        children=[category_summary_type(child) for child in row.get("children", [])],
        attributes=[attribute_type(item) for item in row.get("attributes", [])],
        meta=seo_type(row["meta"]),
    )


def price_type(row: dict[str, Any]) -> PriceType:
    discount = row.get("discount")
    return PriceType(
        amount=row["amount"],
        base_amount=row["base_amount"],
        currency=row["currency"],
        is_discounted=row["is_discounted"],
        discount=(
            AppliedDiscountType(
                id=discount["id"],
                name=discount["name"],
                kind=discount["kind"],
                value=discount["value"],
                amount_off=discount["amount_off"],
                percent_off=discount["percent_off"],
                ends_at=discount["ends_at"],
            )
            if discount
            else None
        ),
    )


def _summary_fields(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": row["id"],
        "slug": row["slug"],
        "name": row["name"],
        "subtitle": row["subtitle"],
        "summary": row["summary"],
        "image": row["image"],
        "brand": row.get("brand"),
        "brand_slug": row.get("brand_slug"),
        "seller": row.get("seller"),
        "seller_slug": row.get("seller_slug"),
        "category": row["category"],
        "category_name": row["category_name"],
        "price": price_type(row["price"]),
        "compare_at_price": row.get("compare_at_price"),
        "in_stock": row["in_stock"],
        "has_variants": row["has_variants"],
        "is_featured": row["is_featured"],
        "rating_average": row["rating_average"],
        "rating_count": row["rating_count"],
        "like_count": row["like_count"],
        "sales_count": row["sales_count"],
        "tags": list(row["tags"]),
        "created_at": row["created_at"],
    }


def product_summary_type(row: dict[str, Any]) -> ProductSummaryType:
    return ProductSummaryType(**_summary_fields(row))


def review_type(row: dict[str, Any]) -> ReviewType:
    return ReviewType(
        id=row["id"],
        product=row.get("product"),
        author=row["author"],
        rating=row["rating"],
        title=row["title"],
        body=row["body"],
        status=row["status"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def product_type(row: dict[str, Any]) -> ProductType:
    dimensions = row.get("dimensions_mm")
    own_review = row.get("own_review")
    return ProductType(
        **_summary_fields(row),
        description=row["description"],
        sku=row["sku"],
        barcode=row["barcode"],
        tax_rate=row["tax_rate"],
        status=row["status"],
        published_at=row.get("published_at"),
        stock=row.get("stock"),
        track_inventory=row["track_inventory"],
        allow_backorder=row["allow_backorder"],
        weight_grams=row.get("weight_grams"),
        dimensions_mm=(
            DimensionsType(
                length=dimensions.get("length"),
                width=dimensions.get("width"),
                height=dimensions.get("height"),
            )
            if dimensions
            else None
        ),
        images=[
            ImageType(
                url=image["url"],
                alt=image["alt"],
                caption=image["caption"],
                is_primary=image["is_primary"],
            )
            for image in row.get("images", [])
        ],
        attributes=[
            ProductAttributeType(
                code=item["code"],
                name=item["name"],
                type=item["type"],
                unit=item["unit"],
                value=item["value"],
            )
            for item in row.get("attributes", [])
        ],
        variant_attributes=[
            variant_attribute_type(item) for item in row.get("variant_attributes", [])
        ],
        variants=[
            VariantType(
                id=variant["id"],
                sku=variant["sku"],
                label=variant["label"],
                options=variant["options"],
                image=variant["image"],
                stock=variant["stock"],
                total_stock=variant["total_stock"],
                in_stock=variant["in_stock"],
                sellers=[offer_type(offer) for offer in variant.get("sellers", [])],
                seller_count=variant["seller_count"],
                price=price_type(variant["price"]),
            )
            for variant in row.get("variants", [])
        ],
        breadcrumbs=breadcrumb_types(row.get("breadcrumbs", [])),
        meta=seo_type(row["meta"]),
        liked=row["liked"],
        own_review=review_type(own_review) if own_review else None,
        sold_by=seller_type(row["sold_by"]) if row.get("sold_by") else None,
        offers=[offer_type(offer) for offer in row.get("offers", [])],
        seller_count=row.get("seller_count", 0),
    )


def product_page_type(page: dict[str, Any]) -> ProductPageType:
    return ProductPageType(
        items=[product_summary_type(item) for item in page["items"]],
        total=page["total"],
        limit=page["limit"],
        offset=page["offset"],
    )


def listing_type(page: dict[str, Any]) -> ListingType:
    return ListingType(
        key=page["key"],
        name=page["name"],
        description=page["description"],
        items=[product_summary_type(item) for item in page["items"]],
        total=page["total"],
        limit=page["limit"],
        offset=page["offset"],
    )


def collection_type(row: dict[str, Any]) -> CollectionType:
    return CollectionType(
        id=row["id"],
        name=row["name"],
        slug=row["slug"],
        description=row["description"],
        image=row["image"],
        products=[product_summary_type(item) for item in row.get("products", [])],
    )


def review_page_type(page: dict[str, Any]) -> ReviewPageType:
    return ReviewPageType(
        items=[review_type(item) for item in page["items"]],
        total=page["total"],
        limit=page["limit"],
        offset=page["offset"],
        rating_average=page.get("rating_average", 0),
        rating_count=page.get("rating_count", 0),
    )


def order_line_type(row: dict[str, Any]) -> OrderLineType:
    return OrderLineType(
        product=row.get("product"),
        name=row["name"],
        seller=row.get("seller"),
        sku=row["sku"],
        quantity=row["quantity"],
        unit_price=row["unit_price"],
        tax_rate=row["tax_rate"],
        line_total=row["line_total"],
    )


def order_type(row: dict[str, Any]) -> OrderType:
    return OrderType(
        number=row["number"],
        status=row["status"],
        status_label=row["status_label"],
        currency=row["currency"],
        placed_at=row["placed_at"],
        items=[order_line_type(item) for item in row["items"]],
        subtotal=row["subtotal"],
        coupon=row.get("coupon"),
        coupon_discount=row["coupon_discount"],
        shipping_method=row.get("shipping_method"),
        shipping_total=row["shipping_total"],
        tax_total=row["tax_total"],
        total=row["total"],
        note=row["note"],
        payment_status=row.get("payment_status"),
        invoice=row.get("invoice"),
        carrier=row.get("carrier", ""),
        tracking_number=row.get("tracking_number", ""),
        tracking_url=row.get("tracking_url", ""),
        shipped_at=row.get("shipped_at"),
        history=[order_event_type(event) for event in row.get("history", [])],
    )


def order_event_type(row: dict[str, Any]) -> OrderEventType:
    return OrderEventType(
        status=row["status"],
        status_label=row["status_label"],
        note=row["note"],
        at=row["at"],
    )


def address_type(row: dict[str, Any]) -> AddressType:
    return AddressType(
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
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def shipping_method_type(row: dict[str, Any]) -> ShippingMethodType:
    return ShippingMethodType(
        id=row["id"],
        name=row["name"],
        description=row["description"],
        price=row["price"],
        cost=row["cost"],
        is_free=row["is_free"],
        free_from=row.get("free_from"),
        min_days=row["min_days"],
        max_days=row["max_days"],
        currency=row["currency"],
    )


def coupon_preview_type(row: dict[str, Any]) -> CouponPreviewType:
    return CouponPreviewType(
        code=row["code"],
        is_valid=row["is_valid"],
        reason=row["reason"],
        discount=row["discount"],
        subtotal=row["subtotal"],
        total=row["total"],
        currency=row["currency"],
    )


def order_page_type(page: dict[str, Any]) -> OrderPageType:
    return OrderPageType(
        items=[order_type(row) for row in page["items"]],
        total=page["total"],
        limit=page["limit"],
        offset=page["offset"],
    )


def invoice_type(row: dict[str, Any]) -> InvoiceType:
    return InvoiceType(
        number=row["number"],
        issued_at=row["issued_at"],
        due_at=row.get("due_at"),
        notes=row["notes"],
        order=order_type(row["order"]),
    )


def cart_type(row: dict[str, Any]) -> CartType:
    return CartType(
        id=row["id"],
        currency=row["currency"],
        items=[
            CartItemType(
                id=item["id"],
                product=item["product"],
                name=item["name"],
                image=item["image"],
                variant=item.get("variant"),
                variant_label=item.get("variant_label"),
                offer=item.get("offer"),
                seller=item.get("seller"),
                quantity=item["quantity"],
                unit_price=price_type(item["unit_price"]),
                line_total=item["line_total"],
                in_stock=item["in_stock"],
            )
            for item in row["items"]
        ],
        item_count=row["item_count"],
        unit_count=row["unit_count"],
        subtotal=row["subtotal"],
        discount_total=row["discount_total"],
        total=row["total"],
        updated_at=row["updated_at"],
    )
