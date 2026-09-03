"""The nested messages the shop service answers with.

Two encodings are worth explaining, because both are decisions rather than
oversights.

**Money travels as a string.** Protobuf has no decimal type. A ``double`` would
make 19.99 into something that is nearly 19.99, and a shop that adds up nearly
prices sells things for nearly the right amount. The alternative -- minor units
as an integer -- needs every client to know the currency's exponent before it
can render anything. A string is exact, is what the REST document carries, and
is what every language's decimal type parses.

**A free-form value travels as JSON.** A product attribute's ``value`` has a
shape decided by its ``type``, which the same message carries, and a variant's
``options`` is a map whose keys are whatever the category declared. There is no
single protobuf type that fits either, so both carry the JSON encoding of what
they hold and a client parses them according to the type beside them.

The discount is flattened into the price rather than nested, so a client asks
``is_discounted`` rather than reasoning about whether an unset message means
"no campaign" or "the campaign has no name".
"""

from rest_framework import serializers


class Price(serializers.Serializer[dict[str, object]]):
    """What a shopper pays, what they would have paid, and which campaign made it so."""

    amount = serializers.CharField()
    base_amount = serializers.CharField()
    currency = serializers.CharField()
    is_discounted = serializers.BooleanField()
    discount_id = serializers.CharField()
    discount_name = serializers.CharField()
    discount_kind = serializers.CharField()
    discount_value = serializers.CharField()
    discount_amount_off = serializers.CharField()
    discount_percent_off = serializers.IntegerField()
    discount_ends_at = serializers.CharField()


class Breadcrumb(serializers.Serializer[dict[str, object]]):
    name = serializers.CharField()
    slug = serializers.CharField()


class Attribute(serializers.Serializer[dict[str, object]]):
    """One thing every product in a category answers, and how."""

    code = serializers.CharField()
    name = serializers.CharField()
    type = serializers.CharField()
    unit = serializers.CharField()
    choices = serializers.ListField(child=serializers.CharField())
    required = serializers.BooleanField()
    is_variant = serializers.BooleanField()
    is_filterable = serializers.BooleanField()
    help_text = serializers.CharField()


class Brand(serializers.Serializer[dict[str, object]]):
    id = serializers.CharField()
    name = serializers.CharField()
    slug = serializers.CharField()
    description = serializers.CharField()
    logo = serializers.CharField()
    website = serializers.CharField()


class CategorySummary(serializers.Serializer[dict[str, object]]):
    """A category, with its parent's slug rather than its children.

    The tree arrives flat and is rebuilt by the client from ``parent``, because
    protobuf has no recursive message shorthand and a self-nesting category
    would have to be declared by hand at a fixed depth.
    """

    id = serializers.CharField()
    name = serializers.CharField()
    slug = serializers.CharField()
    description = serializers.CharField()
    image = serializers.CharField()
    icon = serializers.CharField()
    parent = serializers.CharField()
    product_count = serializers.IntegerField()


class Seller(serializers.Serializer[dict[str, object]]):
    """One seller, as a "sold by" line names them."""

    id = serializers.CharField()
    name = serializers.CharField()
    slug = serializers.CharField()
    description = serializers.CharField()
    logo = serializers.CharField()
    city = serializers.CharField()
    product_count = serializers.IntegerField()


class Offer(serializers.Serializer[dict[str, object]]):
    """One seller's price for one thing, priced now."""

    id = serializers.CharField()
    seller = Seller()
    variant = serializers.CharField()
    sku = serializers.CharField()
    condition = serializers.CharField()
    lead_time_days = serializers.IntegerField()
    stock = serializers.IntegerField()
    in_stock = serializers.BooleanField()
    price = Price()


class ProductImage(serializers.Serializer[dict[str, object]]):
    url = serializers.CharField()
    alt = serializers.CharField()
    caption = serializers.CharField()
    is_primary = serializers.BooleanField()


class ProductAttributeValue(serializers.Serializer[dict[str, object]]):
    """One answered attribute, carrying enough to print a spec row without a lookup."""

    code = serializers.CharField()
    name = serializers.CharField()
    type = serializers.CharField()
    unit = serializers.CharField()
    value_json = serializers.CharField()


class Variant(serializers.Serializer[dict[str, object]]):
    id = serializers.CharField()
    sku = serializers.CharField()
    label = serializers.CharField()
    options_json = serializers.CharField()
    image = serializers.CharField()
    stock = serializers.IntegerField()
    in_stock = serializers.BooleanField()
    price = Price()


class ProductSummary(serializers.Serializer[dict[str, object]]):
    """A card: what a listing, a search result and a related row need."""

    id = serializers.CharField()
    slug = serializers.CharField()
    name = serializers.CharField()
    subtitle = serializers.CharField()
    summary = serializers.CharField()
    image = serializers.CharField()
    brand = serializers.CharField()
    brand_slug = serializers.CharField()
    seller = serializers.CharField()
    seller_slug = serializers.CharField()
    category = serializers.CharField()
    category_name = serializers.CharField()
    price = Price()
    compare_at_price = serializers.CharField()
    in_stock = serializers.BooleanField()
    has_variants = serializers.BooleanField()
    is_featured = serializers.BooleanField()
    rating_average = serializers.CharField()
    rating_count = serializers.IntegerField()
    like_count = serializers.IntegerField()
    sales_count = serializers.IntegerField()
    tags = serializers.ListField(child=serializers.CharField())
    created_at = serializers.CharField()


class Review(serializers.Serializer[dict[str, object]]):
    """One review. The author is a display name, never an address."""

    id = serializers.CharField()
    product = serializers.CharField()
    author = serializers.CharField()
    rating = serializers.IntegerField()
    title = serializers.CharField()
    body = serializers.CharField()
    status = serializers.CharField()
    created_at = serializers.CharField()
    updated_at = serializers.CharField()


class CartItem(serializers.Serializer[dict[str, object]]):
    id = serializers.CharField()
    product = serializers.CharField()
    name = serializers.CharField()
    image = serializers.CharField()
    variant = serializers.CharField()
    variant_label = serializers.CharField()
    offer = serializers.CharField()
    seller = serializers.CharField()
    quantity = serializers.IntegerField()
    unit_price = Price()
    line_total = serializers.CharField()
    in_stock = serializers.BooleanField()


class ProductPage(serializers.Serializer[dict[str, object]]):
    """A pageable group of cards, used by search and every generated list."""

    items = ProductSummary(many=True)
    total = serializers.IntegerField()
    limit = serializers.IntegerField()
    offset = serializers.IntegerField()


class ReviewPage(serializers.Serializer[dict[str, object]]):
    items = Review(many=True)
    total = serializers.IntegerField()
    limit = serializers.IntegerField()
    offset = serializers.IntegerField()
    rating_average = serializers.CharField(required=False)
    rating_count = serializers.IntegerField(required=False)


class Cart(serializers.Serializer[dict[str, object]]):
    """A caller-owned basket, priced at the moment the RPC is answered."""

    id = serializers.CharField()
    currency = serializers.CharField()
    items = CartItem(many=True)
    item_count = serializers.IntegerField()
    subtotal = serializers.CharField()
    discount_total = serializers.CharField()
    total = serializers.CharField()
    updated_at = serializers.CharField()


class ListingSummary(serializers.Serializer[dict[str, object]]):
    key = serializers.CharField()
    name = serializers.CharField()
    description = serializers.CharField()


class CollectionSummary(serializers.Serializer[dict[str, object]]):
    id = serializers.CharField()
    name = serializers.CharField()
    slug = serializers.CharField()
    description = serializers.CharField()
    image = serializers.CharField()


class OrderLine(serializers.Serializer[dict[str, object]]):
    """One line of an order, read off its snapshot rather than the catalogue."""

    product = serializers.CharField()
    name = serializers.CharField()
    seller = serializers.CharField()
    sku = serializers.CharField()
    quantity = serializers.IntegerField()
    unit_price = serializers.CharField()
    tax_rate = serializers.CharField()
    line_total = serializers.CharField()


class Order(serializers.Serializer[dict[str, object]]):
    """One order at the prices that were agreed, and where it stands now."""

    number = serializers.CharField()
    status = serializers.CharField()
    status_label = serializers.CharField()
    currency = serializers.CharField()
    placed_at = serializers.CharField()
    items = OrderLine(many=True)
    subtotal = serializers.CharField()
    coupon = serializers.CharField()
    coupon_discount = serializers.CharField()
    shipping_method = serializers.CharField()
    shipping_total = serializers.CharField()
    tax_total = serializers.CharField()
    total = serializers.CharField()
    note = serializers.CharField()
    payment_status = serializers.CharField()
    invoice = serializers.CharField()


class Invoice(serializers.Serializer[dict[str, object]]):
    """The document, with the order it demands payment for nested whole."""

    number = serializers.CharField()
    issued_at = serializers.CharField()
    due_at = serializers.CharField()
    notes = serializers.CharField()
    order = Order()
