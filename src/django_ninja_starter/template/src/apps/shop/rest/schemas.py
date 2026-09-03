"""The contract the shop endpoints publish.

Written out rather than generated from the models, because the models carry
things a shop does not publish -- ``cost_price`` is the obvious one -- and a
schema derived from a table publishes every column somebody adds to it. Here a
field is on the wire because it is written down in this file.

Money is ``Decimal`` throughout and is serialised as a JSON number. Prices are
quoted in the shop's single currency, which every price object names, so a
client never has to remember which one it asked about.

An attribute's ``value`` is typed ``Any``, and that is the point: its shape is
decided by its ``type``, which the same object carries, so a client switches on
the type and knows exactly what it has. Pinning it to a union of every possible
shape would make the OpenAPI document longer, the generated clients worse, and
would have to be edited the first time a shop invents a new kind of attribute.
"""

from datetime import datetime
from decimal import Decimal
from typing import Any

from ninja import Schema

from apps.shop.attributes import AttributeType


class MessageOut(Schema):
    detail: str


class BreadcrumbOut(Schema):
    name: str
    slug: str


class BrandOut(Schema):
    id: str
    name: str
    slug: str
    description: str = ""
    logo: str = ""
    website: str = ""


class SellerOut(Schema):
    """One seller, as a "sold by" line names them."""

    id: str
    name: str
    slug: str
    description: str = ""
    logo: str = ""
    city: str = ""


class SellerDetailOut(SellerOut):
    product_count: int = 0


class AttributeOut(Schema):
    """One thing every product in a category answers, and how."""

    code: str
    name: str
    type: AttributeType
    unit: str = ""
    choices: list[str] = []
    required: bool = False
    is_variant: bool = False
    is_filterable: bool = True
    help_text: str = ""


class CategorySummaryOut(Schema):
    """A row in a category list. ``id`` is the slug the detail route takes."""

    id: str
    name: str
    slug: str
    description: str = ""
    image: str = ""
    icon: str = ""
    parent: str | None = None
    product_count: int | None = None


class CategoryNodeOut(CategorySummaryOut):
    children: list["CategoryNodeOut"] = []


class SeoOut(Schema):
    title: str
    description: str = ""
    keywords: list[str] = []


class CategoryOut(CategorySummaryOut):
    breadcrumbs: list[BreadcrumbOut] = []
    children: list[CategorySummaryOut] = []
    attributes: list[AttributeOut] = []
    meta: SeoOut


class AppliedDiscountOut(Schema):
    """The campaign a price is under. ``ends_at`` is what a countdown counts to."""

    id: str
    name: str
    kind: str
    value: Decimal
    amount_off: Decimal
    percent_off: int
    ends_at: datetime | None = None


class PriceOut(Schema):
    """What a shopper pays, and what they would have paid."""

    amount: Decimal
    base_amount: Decimal
    currency: str
    is_discounted: bool = False
    discount: AppliedDiscountOut | None = None


class ImageOut(Schema):
    url: str
    alt: str = ""
    caption: str = ""
    is_primary: bool = False


class VariantOut(Schema):
    id: str
    sku: str
    label: str
    options: dict[str, Any] = {}
    image: str = ""
    stock: int = 0
    in_stock: bool = False
    price: PriceOut


class OfferOut(Schema):
    """One seller's offer of one thing, priced now."""

    id: str
    seller: SellerOut
    variant: str | None = None
    sku: str = ""
    condition: str
    lead_time_days: int = 0
    stock: int = 0
    in_stock: bool = True
    price: PriceOut


class ProductAttributeOut(Schema):
    code: str
    name: str
    type: AttributeType
    unit: str = ""
    value: Any = None


class ProductSummaryOut(Schema):
    """A card: what a listing, a search result and a related row need."""

    id: str
    slug: str
    name: str
    subtitle: str = ""
    summary: str = ""
    image: str = ""
    brand: str | None = None
    brand_slug: str | None = None
    seller: str | None = None
    seller_slug: str | None = None
    category: str
    category_name: str
    price: PriceOut
    compare_at_price: Decimal | None = None
    in_stock: bool = True
    has_variants: bool = False
    is_featured: bool = False
    rating_average: Decimal = Decimal("0")
    rating_count: int = 0
    like_count: int = 0
    sales_count: int = 0
    tags: list[str] = []
    created_at: datetime


class DimensionsOut(Schema):
    length: int | None = None
    width: int | None = None
    height: int | None = None


class ReviewOut(Schema):
    """One review. The author is a display name, never an address."""

    id: str
    product: str | None = None
    author: str
    rating: int
    title: str = ""
    body: str = ""
    status: str
    created_at: datetime
    updated_at: datetime


class ProductOut(ProductSummaryOut):
    description: str = ""
    sku: str
    barcode: str = ""
    tax_rate: Decimal = Decimal("0")
    status: str
    published_at: datetime | None = None
    stock: int | None = None
    track_inventory: bool = True
    allow_backorder: bool = False
    weight_grams: int | None = None
    dimensions_mm: DimensionsOut | None = None
    images: list[ImageOut] = []
    attributes: list[ProductAttributeOut] = []
    variant_attributes: list[AttributeOut] = []
    variants: list[VariantOut] = []
    breadcrumbs: list[BreadcrumbOut] = []
    meta: SeoOut
    # This caller's own relationship to the product, so a page does not have to
    # render the heart empty and then fill it in.
    liked: bool = False
    own_review: ReviewOut | None = None
    # Who sells it. `sold_by` is the seller whose price this page quotes, and
    # `offers` is everybody else who could fill the order.
    sold_by: SellerOut | None = None
    offers: list[OfferOut] = []
    seller_count: int = 0


class ProductPageOut(Schema):
    """A page of products and the total, so a client can say "1-24 of 312"."""

    items: list[ProductSummaryOut] = []
    total: int = 0
    limit: int
    offset: int


class ListingSummaryOut(Schema):
    key: str
    name: str
    description: str


class ListingOut(ListingSummaryOut, ProductPageOut):
    pass


class CollectionOut(Schema):
    id: str
    name: str
    slug: str
    description: str = ""
    image: str = ""
    products: list[ProductSummaryOut] = []


class ReviewPageOut(Schema):
    items: list[ReviewOut] = []
    total: int = 0
    limit: int
    offset: int
    rating_average: Decimal = Decimal("0")
    rating_count: int = 0


class MyReviewPageOut(Schema):
    items: list[ReviewOut] = []
    total: int = 0
    limit: int
    offset: int


class ReviewIn(Schema):
    rating: int
    title: str = ""
    body: str = ""


class ReviewRemovedOut(Schema):
    product: str
    rating_average: Decimal
    rating_count: int


class LikeOut(Schema):
    product: str
    liked: bool
    like_count: int


class CartItemOut(Schema):
    id: str
    product: str
    name: str
    image: str = ""
    variant: str | None = None
    variant_label: str | None = None
    offer: str | None = None
    seller: str | None = None
    quantity: int
    unit_price: PriceOut
    line_total: Decimal
    in_stock: bool = True


class CartOut(Schema):
    """The basket, priced now rather than when things were added to it."""

    id: str
    currency: str
    items: list[CartItemOut] = []
    item_count: int = 0
    unit_count: int = 0
    subtotal: Decimal
    discount_total: Decimal
    total: Decimal
    updated_at: datetime


class CartAddIn(Schema):
    product: str
    variant: str | None = None
    # Which seller to buy from. Left out, the basket takes the one the product
    # page was showing -- the cheapest that can fill the order.
    offer: str | None = None
    quantity: int = 1


class CartQuantityIn(Schema):
    quantity: int


class CheckoutIn(Schema):
    address: str
    shipping_method: str
    coupon: str = ""
    note: str = ""
    provider: str = "manual"


class PaymentConfirmIn(Schema):
    reference: str = ""


class OrderLineOut(Schema):
    product: str | None = None
    name: str
    seller: str | None = None
    sku: str = ""
    quantity: int
    unit_price: Decimal
    tax_rate: Decimal = Decimal("0")
    line_total: Decimal


class OrderOut(Schema):
    """One order, read entirely off its own snapshot."""

    number: str
    status: str
    status_label: str
    currency: str
    placed_at: datetime
    items: list[OrderLineOut] = []
    subtotal: Decimal
    coupon: str | None = None
    coupon_discount: Decimal
    shipping_method: str | None = None
    shipping_total: Decimal
    tax_total: Decimal
    total: Decimal
    shipping_address: dict[str, str] = {}
    note: str = ""
    payment_status: str | None = None
    invoice: str | None = None


class OrderPageOut(Schema):
    items: list[OrderOut] = []
    total: int = 0
    limit: int
    offset: int


class InvoiceOut(Schema):
    """The document, with the order it demands payment for nested whole."""

    number: str
    issued_at: datetime
    due_at: datetime | None = None
    notes: str = ""
    order: OrderOut


class ViewOut(Schema):
    product: str
    view_count: int


CategoryNodeOut.model_rebuild()
