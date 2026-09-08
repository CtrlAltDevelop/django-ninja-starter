"""The admin, which is where this shop's catalogue is written.

There is no endpoint that creates a product, a category or a discount. That is
a decision rather than an omission -- a shop's catalogue is its balance sheet,
and an API that writes to it needs an authorisation model this app does not have
-- and it means these screens are not a convenience. They are the product's only
editing surface, so they are built as one.

Three ideas shape them.

**A category is designed; a product is filled in.** Which attributes a category
declares, of what type, which are required and which distinguish variants, is a
decision with consequences for whoever renders the storefront. So the attribute
schema is edited on the category, inline, by whoever designs the catalogue --
and a product's screen offers exactly the attributes its category declared, and
says out loud which required ones are still empty.

**Every number a shopkeeper glances at is on the list screen.** The price and
what a discount currently makes of it, stock and whether it is running low, the
rating and how many people left one. A list that shows only names is a list that
has to be clicked through to answer any question at all, and the queries behind
these columns are annotated once for the page rather than run per row.

**A record of something that happened is not editable.** Carts and likes are
read-only: they are what shoppers did, and a record that can be typed in by hand
is not a record. Reviews are the exception, and only in one direction -- a
moderator changes their status and writes down why, and cannot rewrite what
somebody said.

**Payment is settled here, because no gateway is wired up.** Orders are placed
against the ``manual`` provider, and somebody with a bank statement in front of
them marks the payment taken or refused. Those are *actions* rather than an
editable status field: marking an order paid also turns its stock reservations
into sales, and a status somebody could type over would let the two disagree.
When a project does wire a gateway up, its callback settles the order through
exactly the same service method these actions call.

The theme is whatever :mod:`apps.shop.theme` resolves: Unfold where the project
installs it, Django's own admin where it does not.
"""

from datetime import timedelta
from typing import Any

from django.contrib import admin, messages
from django.db.models import Case, Count, F, Prefetch, Q, QuerySet, Sum, When
from django.http import HttpRequest
from django.utils import timezone
from django.utils.html import format_html, format_html_join

from apps.shop import options
from apps.shop.models import (
    Address,
    Brand,
    Cart,
    CartItem,
    Category,
    CategoryAttribute,
    Collection,
    CollectionItem,
    Coupon,
    Discount,
    InventoryReservation,
    Invoice,
    Order,
    OrderEvent,
    OrderItem,
    OrderStatus,
    Payment,
    PaymentStatus,
    Product,
    ProductAttribute,
    ProductImage,
    ProductLike,
    ProductOffer,
    ProductStatus,
    ProductVariant,
    Review,
    ReviewStatus,
    Seller,
    ShippingMethod,
    Tag,
)
from apps.shop.pricing import price_of, running_discounts
from apps.shop.services import ShopRefused, shop_service
from apps.shop.theme import (
    BooleanRadioFilter,
    ChoicesDropdownFilter,
    ModelAdmin,
    RelatedDropdownFilter,
    TabularInline,
    dropdown_filter,
)

GREEN = "#16a34a"
AMBER = "#d97706"
RED = "#dc2626"
GREY = "#6b7280"


BLUE = "#2563eb"

#: What colour each order status is drawn in, everywhere it is drawn. One
#: dictionary rather than three, because an order that is amber on the order
#: list and green on the invoice list is a shop with two ideas of what "paid"
#: looks like.
#: Keyed by ``str(...)`` for the same reason as ``ORDER_TRANSITIONS``: the value
#: in the column is the member's string, not the member.
ORDER_COLOURS = {
    str(OrderStatus.PENDING): AMBER,
    str(OrderStatus.PAID): GREEN,
    str(OrderStatus.PROCESSING): BLUE,
    str(OrderStatus.SHIPPED): BLUE,
    str(OrderStatus.COMPLETED): GREEN,
    str(OrderStatus.CANCELLED): GREY,
    str(OrderStatus.REFUNDED): RED,
}


def _swatch(colour: str, text: str) -> Any:
    return format_html('<span style="color: {}; font-weight: 600">{}</span>', colour, text)


def _pill(colour: str, text: str) -> Any:
    """A badge, for the columns somebody scans down rather than reads."""
    return format_html(
        '<span style="background: {}1a; color: {}; border: 1px solid {}55; '
        "border-radius: 999px; padding: 1px 8px; font-size: 11px; font-weight: 600; "
        'white-space: nowrap">{}</span>',
        colour,
        colour,
        colour,
        text,
    )


def _money(amount: Any, currency: str = "") -> str:
    return f"{amount} {currency}".strip()


def _apply_transition(
    model_admin: Any,
    request: HttpRequest,
    queryset: QuerySet[Any],
    step: Any,
    past_tense: str,
    level: int = messages.SUCCESS,
) -> None:
    """Run one order step over a selection, and report both halves of the result.

    Per row rather than as one ``update``, because none of these steps is a word
    in a column: cancelling and refunding put stock back and refunding closes
    the payments. A refusal is reported rather than raised -- somebody who
    selected fifteen orders wants the twelve that could move to have moved.
    """
    done, refused = 0, []
    for order in queryset.select_related("user"):
        try:
            step(order, actor=request.user)
        except ShopRefused as refusal:
            refused.append(f"{order.number}: {refusal}")
        else:
            done += 1
    if done:
        model_admin.message_user(request, f"{done} {past_tense}.", level)
    for problem in refused:
        model_admin.message_user(request, problem, messages.ERROR)


class ReadOnlyAdmin(ModelAdmin):
    """A record of something that happened, so nothing here is editable.

    Deletion stays available: a shop that has to remove somebody's data on
    request needs a way to, and refusing that in the name of an audit trail
    solves the wrong problem.
    """

    read_only_admin = True

    def has_add_permission(self, request: HttpRequest) -> bool:
        return False

    def has_change_permission(self, request: HttpRequest, obj: Any = None) -> bool:
        return False


# ----------------------------------------------------------------------
# Brands, tags, and the category tree that decides what a product is.
# ----------------------------------------------------------------------


@admin.register(Brand)
class BrandAdmin(ModelAdmin):
    list_display = ("name", "product_count", "is_active", "order")
    list_filter = (dropdown_filter("is_active", BooleanRadioFilter),)
    list_editable = ("order",)
    search_fields = ("name", "slug", "description")
    prepopulated_fields = {"slug": ("name",)}
    readonly_fields = ("id", "created_at", "updated_at")
    fieldsets = (
        (None, {"fields": ("name", "slug", "description", "is_active", "order")}),
        ("Links", {"fields": ("logo", "website")}),
        ("Record", {"classes": ("collapse",), "fields": ("id", "created_at", "updated_at")}),
    )

    def get_queryset(self, request: HttpRequest) -> QuerySet[Brand]:
        return super().get_queryset(request).annotate(products_total=Count("products"))

    @admin.display(description="Products", ordering="products_total")
    def product_count(self, brand: Brand) -> int:
        return getattr(brand, "products_total", 0)


@admin.register(Seller)
class SellerAdmin(ModelAdmin):
    """The shops within the shop, and how much each of them carries."""

    list_display = ("name", "city", "listing_count", "offer_count", "is_active", "order")
    list_filter = (dropdown_filter("is_active", BooleanRadioFilter), "city")
    search_fields = ("name", "slug", "email", "city", "description")
    prepopulated_fields = {"slug": ("name",)}
    autocomplete_fields = ("owner",)
    readonly_fields = ("id", "created_at", "updated_at")
    fieldsets = (
        (None, {"fields": ("name", "slug", "description", "logo", "is_active", "order")}),
        ("Contact", {"fields": ("email", "phone", "city")}),
        ("Account", {"fields": ("owner",), "classes": ("collapse",)}),
        ("Record", {"fields": ("id", "created_at", "updated_at"), "classes": ("collapse",)}),
    )

    def get_queryset(self, request: HttpRequest) -> QuerySet[Seller]:
        return (
            super()
            .get_queryset(request)
            .annotate(listings=Count("products", distinct=True))
            .annotate(offered=Count("offers", distinct=True))
        )

    @admin.display(description="Own listings", ordering="listings")
    def listing_count(self, seller: Seller) -> int:
        return getattr(seller, "listings", 0)

    @admin.display(description="Offers on others", ordering="offered")
    def offer_count(self, seller: Seller) -> int:
        return getattr(seller, "offered", 0)


@admin.register(Tag)
class TagAdmin(ModelAdmin):
    list_display = ("name", "slug", "product_count")
    search_fields = ("name", "slug")
    prepopulated_fields = {"slug": ("name",)}

    def get_queryset(self, request: HttpRequest) -> QuerySet[Tag]:
        return super().get_queryset(request).annotate(products_total=Count("products"))

    @admin.display(description="Products", ordering="products_total")
    def product_count(self, tag: Tag) -> int:
        return getattr(tag, "products_total", 0)


class CategoryAttributeInline(TabularInline):
    """The shape of the products in this category, edited where it is decided."""

    model = CategoryAttribute
    extra = 0
    fields = (
        "name",
        "code",
        "attribute_type",
        "unit",
        "choices",
        "required",
        "is_variant",
        "is_filterable",
        "order",
    )
    ordering = ("order", "name")


@admin.register(Category)
class CategoryAdmin(ModelAdmin):
    """Where the tree is arranged and each branch's product shape is designed."""

    list_display = ("tree_name", "attribute_count", "product_count", "is_active", "order")
    list_filter = (
        dropdown_filter("is_active", BooleanRadioFilter),
        dropdown_filter("parent", RelatedDropdownFilter),
    )
    list_editable = ("order",)
    search_fields = ("name", "slug", "description")
    prepopulated_fields = {"slug": ("name",)}
    autocomplete_fields = ("parent",)
    inlines = (CategoryAttributeInline,)
    readonly_fields = ("id", "created_at", "updated_at", "inherited_attributes")
    fieldsets = (
        (None, {"fields": ("name", "slug", "parent", "description", "is_active", "order")}),
        ("Appearance", {"fields": ("image", "icon")}),
        (
            "Inherited",
            {
                "description": (
                    "Attributes declared further up the tree. Products here answer "
                    "these as well as the ones below."
                ),
                "fields": ("inherited_attributes",),
            },
        ),
        (
            "Search engines",
            {
                "classes": ("collapse",),
                "fields": ("meta_title", "meta_description", "meta_keywords"),
            },
        ),
        ("Record", {"classes": ("collapse",), "fields": ("id", "created_at", "updated_at")}),
    )

    def get_queryset(self, request: HttpRequest) -> QuerySet[Category]:
        return (
            super()
            .get_queryset(request)
            .select_related("parent__parent")
            .annotate(products_total=Count("products", distinct=True))
            .annotate(attributes_total=Count("attributes", distinct=True))
        )

    @admin.display(description="Category", ordering="name")
    def tree_name(self, category: Category) -> str:
        """The whole path, because "Accessories" alone appears four times in a shop."""
        return " / ".join(node.name for node in category.ancestors(including_self=True))

    @admin.display(description="Attributes", ordering="attributes_total")
    def attribute_count(self, category: Category) -> int:
        return getattr(category, "attributes_total", 0)

    @admin.display(description="Products", ordering="products_total")
    def product_count(self, category: Category) -> int:
        return getattr(category, "products_total", 0)

    @admin.display(description="From further up")
    def inherited_attributes(self, category: Category) -> str:
        """What this category's products answer without it declaring anything.

        Shown because the alternative is somebody declaring "warranty" here for
        the third time and wondering why the storefront shows one.
        """
        if not category.pk:
            return "-"
        own = {attribute.pk for attribute in category.attributes.all()}
        inherited = [
            f"{attribute.name} ({attribute.category.name})"
            for attribute in category.attribute_schema()
            if attribute.pk not in own
        ]
        return ", ".join(inherited) or "Nothing -- this is a top-level shape."


@admin.register(CategoryAttribute)
class CategoryAttributeAdmin(ModelAdmin):
    """Registered so the attribute picker on a product can search it.

    Editing is normally done inline on the category, which is where the shape is
    designed; this screen is for finding out which category declared the one
    causing trouble.
    """

    list_display = ("name", "category", "attribute_type", "required", "is_variant", "order")
    list_filter = (
        dropdown_filter("attribute_type", ChoicesDropdownFilter),
        "required",
        "is_variant",
    )
    search_fields = ("name", "code", "category__name")
    autocomplete_fields = ("category",)
    prepopulated_fields = {"code": ("name",)}


# ----------------------------------------------------------------------
# Products.
# ----------------------------------------------------------------------


class ProductImageInline(TabularInline):
    model = ProductImage
    extra = 0
    fields = ("url", "alt", "caption", "is_primary", "order")
    ordering = ("-is_primary", "order")


class ProductVariantInline(TabularInline):
    """The buyable versions. Stock lives here for a product that has any."""

    model = ProductVariant
    extra = 0
    fields = ("sku", "name", "options", "price", "stock", "image", "is_active", "order")
    ordering = ("order", "sku")


class ProductAttributeInline(TabularInline):
    """The product's answers to the attributes its category declared."""

    model = ProductAttribute
    extra = 0
    fields = ("attribute", "value")

    def formfield_for_foreignkey(self, db_field: Any, request: HttpRequest, **kwargs: Any) -> Any:
        """Offer only the attributes this product's category actually declares.

        Without this the dropdown lists every attribute in the shop and the
        model has to reject most of what it offered -- which is a validation
        error where a shorter list would have done. A variant attribute is left
        out because a variant answers it, not the product.
        """
        if db_field.name == "attribute":
            product_id = (
                request.resolver_match.kwargs.get("object_id") if request.resolver_match else None
            )
            product = Product.objects.filter(pk=product_id).select_related("category").first()
            if product is None:
                kwargs["queryset"] = CategoryAttribute.objects.none()
            else:
                allowed = [
                    attribute.pk
                    for attribute in product.category.attribute_schema()
                    if not attribute.is_variant
                ]
                kwargs["queryset"] = CategoryAttribute.objects.filter(pk__in=allowed).order_by(
                    "order", "name"
                )
        return super().formfield_for_foreignkey(db_field, request, **kwargs)


class ProductOfferInline(TabularInline):
    """The other sellers of this product, edited where the product is.

    On the product rather than only on its own screen, because "who else sells
    this, and for how much" is a question asked while looking at the thing --
    and because an offer with no product to belong to is not a row anybody wants
    to create.
    """

    model = ProductOffer
    extra = 0
    fields = (
        "seller",
        "variant",
        "sku",
        "price",
        "stock",
        "condition",
        "lead_time_days",
        "is_active",
    )
    autocomplete_fields = ("seller",)
    show_change_link = True


@admin.register(ProductOffer)
class ProductOfferAdmin(ModelAdmin):
    """Every seller's price for everything, for the times that is the question."""

    list_display = (
        "product",
        "seller",
        "variant",
        "live_price",
        "stock_state",
        "condition",
        "is_active",
    )
    list_filter = (
        dropdown_filter("seller", RelatedDropdownFilter),
        "condition",
        dropdown_filter("is_active", BooleanRadioFilter),
        dropdown_filter("product__category", RelatedDropdownFilter),
    )
    search_fields = ("product__name", "product__sku", "seller__name", "sku")
    autocomplete_fields = ("product", "seller", "variant")
    readonly_fields = ("id", "created_at", "updated_at")

    def get_queryset(self, request: HttpRequest) -> QuerySet[ProductOffer]:
        return super().get_queryset(request).select_related("product", "seller", "variant")

    @admin.display(description="Price", ordering="price")
    def live_price(self, offer: ProductOffer) -> Any:
        """What this seller is charging now, campaigns included."""
        price = price_of(offer.product, offer.variant, offer=offer)
        if not price.is_discounted or price.discount is None:
            return f"{price.amount} {price.currency}"
        return _swatch(GREEN, f"{price.amount} (was {price.base_amount})")

    @admin.display(description="Stock", ordering="stock")
    def stock_state(self, offer: ProductOffer) -> Any:
        if offer.stock <= 0:
            return _swatch(RED, "None")
        return _swatch(GREEN if offer.stock > 10 else AMBER, str(offer.stock))


#: What "how many are on the shelf" means as a database expression rather than a
#: Python property. ``available_stock`` counts a product's variants where it has
#: them and its own column where it does not, and an ordering cannot call a
#: property -- so the same rule is written once, here, and annotated onto any
#: queryset that has to filter or sort by it.
ON_HAND = Case(
    When(
        has_variants=True,
        then=Sum(
            "variants__stock",
            filter=Q(variants__is_active=True),
            distinct=False,
        ),
    ),
    default=F("stock"),
)


class StockFilter(admin.SimpleListFilter):
    """The question a shopkeeper asks first thing in the morning.

    Counted against each product's own ``low_stock_threshold`` rather than one
    number for the whole shop, because "running low" is four for a fridge and
    four hundred for a screw -- and the column that says so is already on the
    product. Variants are counted too: a shirt is out of stock when every size
    is, and a filter that only looked at the product's own column would say a
    shop with nothing left to sell had nothing wrong with it.
    """

    title = "Stock"
    parameter_name = "stock_state"

    def lookups(self, request: HttpRequest, model_admin: Any) -> list[tuple[str, str]]:
        return [
            ("out", "Out of stock"),
            ("low", "Running low"),
            ("in", "In stock"),
            ("untracked", "Not tracked"),
        ]

    def queryset(self, request: HttpRequest, queryset: QuerySet[Any]) -> QuerySet[Any]:
        chosen = self.value()
        if chosen is None:
            return queryset
        if chosen == "untracked":
            return queryset.filter(track_inventory=False)
        if chosen == "in":
            return queryset.in_stock()
        counted = queryset.filter(track_inventory=True).annotate(on_hand=ON_HAND)
        if chosen == "out":
            return counted.filter(Q(on_hand__lte=0) | Q(on_hand__isnull=True))
        if chosen == "low":
            return counted.filter(on_hand__gt=0, on_hand__lte=F("low_stock_threshold"))
        return queryset


class NeedsAttentionFilter(admin.SimpleListFilter):
    """The products somebody has to go and finish.

    A shop's catalogue rots quietly: a product published with no picture, a
    price of nothing, a draft somebody forgot. None of these is invalid -- the
    model lets every one of them be saved on purpose, because a product is
    designed before it is filled in -- so they are a filter rather than a
    constraint, in the one place somebody is in a position to fix them.
    """

    title = "Needs attention"
    parameter_name = "attention"

    def lookups(self, request: HttpRequest, model_admin: Any) -> list[tuple[str, str]]:
        return [
            ("no_image", "No picture"),
            ("no_price", "Priced at nothing"),
            ("stale_draft", "Draft, untouched for 30 days"),
        ]

    def queryset(self, request: HttpRequest, queryset: QuerySet[Any]) -> QuerySet[Any]:
        chosen = self.value()
        if chosen == "no_image":
            return queryset.filter(images__isnull=True)
        if chosen == "no_price":
            return queryset.filter(price__lte=0)
        if chosen == "stale_draft":
            return queryset.filter(
                status=ProductStatus.DRAFT,
                updated_at__lt=timezone.now() - timedelta(days=30),
            )
        return queryset


@admin.register(Product)
class ProductAdmin(ModelAdmin):
    """One screen with everything about a product on it, and nothing about anything else."""

    list_display = (
        "name",
        "category",
        "brand",
        "seller",
        "live_price",
        "stock_state",
        "rating_badge",
        "like_count",
        "status_badge",
    )
    list_filter = (
        dropdown_filter("status", ChoicesDropdownFilter),
        dropdown_filter("category", RelatedDropdownFilter),
        dropdown_filter("brand", RelatedDropdownFilter),
        dropdown_filter("seller", RelatedDropdownFilter),
        StockFilter,
        NeedsAttentionFilter,
        "is_featured",
        "has_variants",
        "created_at",
    )
    search_fields = ("name", "slug", "sku", "barcode", "summary", "description", "brand__name")
    prepopulated_fields = {"slug": ("name",)}
    autocomplete_fields = ("category", "brand", "seller")
    filter_horizontal = ("tags",)
    date_hierarchy = "created_at"
    inlines = (
        ProductImageInline,
        ProductVariantInline,
        ProductAttributeInline,
        ProductOfferInline,
    )
    actions = ("publish", "unpublish", "archive", "feature", "unfeature")
    readonly_fields = (
        "id",
        "created_at",
        "updated_at",
        "rating_average",
        "rating_count",
        "like_count",
        "view_count",
        "current_price",
        "incomplete_attributes",
    )
    # A product is edited rarely and carefully, and a half-finished one lost to a
    # stray back button is an afternoon gone.
    warn_unsaved_form = True
    list_filter_submit = True
    fieldsets = (
        (
            None,
            {
                "fields": (
                    "name",
                    "slug",
                    "category",
                    "brand",
                    "seller",
                    "subtitle",
                    "summary",
                    "description",
                    "tags",
                )
            },
        ),
        (
            "Publishing",
            {
                "description": (
                    "A draft is invisible to the API. An archived product stays "
                    "readable by its slug, because somebody's bookmark and "
                    "somebody's review both point at it."
                ),
                "fields": ("status", "published_at", "is_featured", "order"),
            },
        ),
        (
            "Price",
            {
                "description": (
                    "This is the price before any campaign. Discounts are their own "
                    "rows, with dates, so a sale ends because a clock passed it."
                ),
                "fields": ("price", "compare_at_price", "cost_price", "tax_rate", "current_price"),
            },
        ),
        (
            "Stock",
            {
                "description": (
                    "A product sold in variants carries no stock of its own -- each "
                    "variant carries its own, below."
                ),
                "fields": (
                    "sku",
                    "barcode",
                    "has_variants",
                    "track_inventory",
                    "stock",
                    "low_stock_threshold",
                    "allow_backorder",
                ),
            },
        ),
        (
            "Shipping",
            {
                "classes": ("collapse",),
                "fields": ("weight_grams", "length_mm", "width_mm", "height_mm"),
            },
        ),
        (
            "Search engines",
            {
                "classes": ("collapse",),
                "fields": ("meta_title", "meta_description", "meta_keywords"),
            },
        ),
        (
            "How it is doing",
            {
                "description": "Derived from reviews, likes and orders. Not editable here.",
                "fields": (
                    "incomplete_attributes",
                    "rating_average",
                    "rating_count",
                    "like_count",
                    "view_count",
                    "sales_count",
                ),
            },
        ),
        ("Record", {"classes": ("collapse",), "fields": ("id", "created_at", "updated_at")}),
    )

    def get_queryset(self, request: HttpRequest) -> QuerySet[Product]:
        """Everything the list columns read, in one query rather than five per row."""
        return (
            super()
            .get_queryset(request)
            .select_related("category", "brand", "seller")
            .prefetch_related("variants", "offers__seller")
        )

    def _discounts(self, request: HttpRequest) -> list[Any]:
        """The running campaigns, loaded once for the whole page.

        Cached on the request because ``live_price`` is called per row and the
        answer is the same for all of them.
        """
        cached = getattr(request, "_shop_discounts", None)
        if cached is None:
            cached = running_discounts()
            request._shop_discounts = cached
        return cached

    def get_changelist_instance(self, request: HttpRequest) -> Any:
        self._request = request
        return super().get_changelist_instance(request)

    @admin.display(description="Price", ordering="price")
    def live_price(self, product: Product) -> Any:
        """What a shopper is charged right now, and the campaign making it so."""
        request = getattr(self, "_request", None)
        discounts = self._discounts(request) if request is not None else running_discounts()
        price = price_of(product, None, discounts)
        if not price.is_discounted or price.discount is None:
            return f"{price.amount} {price.currency}"
        return format_html(
            '<span style="text-decoration: line-through; color: {}">{}</span> '
            '<span style="color: {}; font-weight: 600">{}</span> <small>{}</small>',
            GREY,
            price.base_amount,
            GREEN,
            price.amount,
            price.discount.name,
        )

    @admin.display(description="Stock")
    def stock_state(self, product: Product) -> Any:
        if not product.track_inventory:
            return _swatch(GREY, "Not tracked")
        available = product.available_stock
        if available <= 0:
            return _swatch(RED, "Backorder" if product.allow_backorder else "Out of stock")
        if available <= product.low_stock_threshold:
            return _swatch(AMBER, f"{available} left")
        return _swatch(GREEN, str(available))

    @admin.display(description="Rating", ordering="rating_average")
    def rating_badge(self, product: Product) -> str:
        if not product.rating_count:
            return "-"
        return f"{product.rating_average} ({product.rating_count})"

    @admin.display(description="Status", ordering="status")
    def status_badge(self, product: Product) -> Any:
        colours = {
            ProductStatus.ACTIVE: GREEN,
            ProductStatus.DRAFT: AMBER,
            ProductStatus.ARCHIVED: GREY,
        }
        label = product.get_status_display()
        if product.status == ProductStatus.ACTIVE and not product.is_live:
            return _swatch(AMBER, f"Scheduled for {product.published_at:%d %b}")
        return _swatch(colours.get(product.status, GREY), label)

    @admin.display(description="Current price")
    def current_price(self, product: Product) -> str:
        """The same answer as the list column, on the form, so a campaign is visible here."""
        if not product.pk:
            return "-"
        price = price_of(product)
        if not price.is_discounted or price.discount is None:
            return f"{price.amount} {price.currency} -- no campaign is running on this."
        return (
            f"{price.amount} {price.currency} (was {price.base_amount}) under {price.discount.name}"
        )

    @admin.display(description="Still to fill in")
    def incomplete_attributes(self, product: Product) -> Any:
        """Which required attributes of this category nobody has answered.

        The one place the schema's ``required`` flag is enforced, because it is
        the only place somebody is in a position to answer it.
        """
        if not product.pk:
            return "-"
        missing = product.missing_attributes()
        if not missing:
            return _swatch(GREEN, "Nothing -- every required attribute is answered.")
        return _swatch(AMBER, ", ".join(attribute.name for attribute in missing))

    @admin.action(description="Publish")
    def publish(self, request: HttpRequest, queryset: QuerySet[Product]) -> None:
        changed = queryset.update(status=ProductStatus.ACTIVE, published_at=None)
        self.message_user(request, f"{changed} published.", messages.SUCCESS)

    @admin.action(description="Return to draft")
    def unpublish(self, request: HttpRequest, queryset: QuerySet[Product]) -> None:
        changed = queryset.update(status=ProductStatus.DRAFT)
        self.message_user(request, f"{changed} returned to draft.", messages.SUCCESS)

    @admin.action(description="Archive")
    def archive(self, request: HttpRequest, queryset: QuerySet[Product]) -> None:
        """Archived rather than deleted, because reviews and carts point at these."""
        changed = queryset.update(status=ProductStatus.ARCHIVED)
        self.message_user(request, f"{changed} archived.", messages.SUCCESS)

    @admin.action(description="Mark as featured")
    def feature(self, request: HttpRequest, queryset: QuerySet[Product]) -> None:
        changed = queryset.update(is_featured=True)
        self.message_user(request, f"{changed} featured.", messages.SUCCESS)

    @admin.action(description="Remove from featured")
    def unfeature(self, request: HttpRequest, queryset: QuerySet[Product]) -> None:
        changed = queryset.update(is_featured=False)
        self.message_user(request, f"{changed} no longer featured.", messages.SUCCESS)


@admin.register(ProductVariant)
class ProductVariantAdmin(ModelAdmin):
    """Registered mostly so a cart line can link to one and stock can be searched."""

    list_display = ("sku", "product", "label", "price", "stock", "is_active")
    list_filter = ("is_active", dropdown_filter("product__category", RelatedDropdownFilter))
    search_fields = ("sku", "name", "product__name")
    autocomplete_fields = ("product",)

    def get_queryset(self, request: HttpRequest) -> QuerySet[ProductVariant]:
        return super().get_queryset(request).select_related("product")


# ----------------------------------------------------------------------
# Campaigns and curation.
# ----------------------------------------------------------------------


class CollectionItemInline(TabularInline):
    model = CollectionItem
    extra = 0
    fields = ("product", "order")
    ordering = ("order",)
    autocomplete_fields = ("product",)


@admin.register(Collection)
class CollectionAdmin(ModelAdmin):
    """A list arranged by hand, for the thing no ordering of the data can say."""

    list_display = ("name", "product_count", "is_active", "order")
    list_filter = (dropdown_filter("is_active", BooleanRadioFilter),)
    list_editable = ("order",)
    search_fields = ("name", "slug", "description")
    prepopulated_fields = {"slug": ("name",)}
    inlines = (CollectionItemInline,)

    def get_queryset(self, request: HttpRequest) -> QuerySet[Collection]:
        return super().get_queryset(request).annotate(products_total=Count("items"))

    @admin.display(description="Products", ordering="products_total")
    def product_count(self, collection: Collection) -> int:
        return getattr(collection, "products_total", 0)


@admin.register(Discount)
class DiscountAdmin(ModelAdmin):
    """A campaign: this much off these things, between these dates."""

    list_display = ("name", "offer", "window", "running", "priority", "is_active")
    list_filter = (dropdown_filter("kind", ChoicesDropdownFilter), "is_active", "applies_to_all")
    search_fields = ("name", "description")
    filter_horizontal = ("products", "categories")
    date_hierarchy = "starts_at"
    readonly_fields = ("id", "created_at", "updated_at")
    fieldsets = (
        (None, {"fields": ("name", "description", "is_active", "priority")}),
        (
            "The offer",
            {
                "description": (
                    "When two campaigns reach the same product, the one that saves "
                    "the shopper most applies -- and only that one. Priority breaks "
                    "a tie."
                ),
                "fields": ("kind", "value", "max_amount"),
            },
        ),
        (
            "When it runs",
            {
                "description": "Leave either end empty for a campaign with no start or no end.",
                "fields": ("starts_at", "ends_at"),
            },
        ),
        (
            "What it applies to",
            {
                "description": "A category includes everything filed underneath it.",
                "fields": ("applies_to_all", "categories", "products"),
            },
        ),
        ("Record", {"classes": ("collapse",), "fields": ("id", "created_at", "updated_at")}),
    )

    @admin.display(description="Offer")
    def offer(self, discount: Discount) -> str:
        if discount.kind == "percent":
            capped = f", up to {discount.max_amount}" if discount.max_amount else ""
            return f"{discount.value}% off{capped}"
        return f"{discount.value} {options.currency()} off"

    @admin.display(description="Window")
    def window(self, discount: Discount) -> str:
        start = f"{discount.starts_at:%d %b %Y}" if discount.starts_at else "always"
        end = f"{discount.ends_at:%d %b %Y}" if discount.ends_at else "no end"
        return f"{start} - {end}"

    @admin.display(description="Now", boolean=True)
    def running(self, discount: Discount) -> bool:
        return discount.is_running


# ----------------------------------------------------------------------
# What shoppers did. Records, so read-only -- except a moderator's verdict.
# ----------------------------------------------------------------------


class CartItemInline(TabularInline):
    """What is in the basket, including which seller each line is from.

    ``offer`` is in here because without it two lines of the same product read
    as a duplicate rather than as what they are: the same thing from two
    different sellers, at two different prices.
    """

    model = CartItem
    extra = 0
    fields = ("product", "variant", "offer", "quantity", "created_at")
    readonly_fields = fields
    can_delete = False

    def has_add_permission(self, request: HttpRequest, obj: Any = None) -> bool:
        return False


@admin.register(Cart)
class CartAdmin(ReadOnlyAdmin):
    """Whose basket, holding what. A support screen, not an editing one."""

    list_display = ("user", "line_count", "unit_count", "updated_at")
    search_fields = ("user__username", "user__email")
    date_hierarchy = "updated_at"
    inlines = (CartItemInline,)

    def get_queryset(self, request: HttpRequest) -> QuerySet[Cart]:
        return (
            super()
            .get_queryset(request)
            .select_related("user")
            .prefetch_related(
                Prefetch("items", queryset=CartItem.objects.select_related("product"))
            )
            .annotate(lines=Count("items"))
        )

    @admin.display(description="Lines", ordering="lines")
    def line_count(self, cart: Cart) -> int:
        return getattr(cart, "lines", 0)

    @admin.display(description="Units")
    def unit_count(self, cart: Cart) -> int:
        return sum(item.quantity for item in cart.items.all())


@admin.register(Review)
class ReviewAdmin(ModelAdmin):
    """The moderation queue.

    A moderator changes a review's status and writes down why. What the shopper
    actually said is not editable here -- a shop that can rewrite its reviews
    does not have reviews.
    """

    list_display = ("product", "author", "stars", "title", "status_badge", "created_at")
    list_filter = (
        dropdown_filter("status", ChoicesDropdownFilter),
        "rating",
        "created_at",
        dropdown_filter("product__category", RelatedDropdownFilter),
    )
    search_fields = ("title", "body", "product__name", "user__username", "user__email")
    date_hierarchy = "created_at"
    actions = ("approve", "reject")
    autocomplete_fields = ("product",)
    readonly_fields = (
        "id",
        "product",
        "user",
        "rating",
        "title",
        "body",
        "created_at",
        "updated_at",
    )
    fieldsets = (
        (
            "What was written",
            {"fields": ("product", "user", "rating", "title", "body", "created_at")},
        ),
        (
            "Moderation",
            {
                "description": "A rejected review stays, with the reason. It is never published.",
                "fields": ("status", "moderator_note"),
            },
        ),
        ("Record", {"classes": ("collapse",), "fields": ("id", "updated_at")}),
    )

    def get_queryset(self, request: HttpRequest) -> QuerySet[Review]:
        return super().get_queryset(request).select_related("product", "user")

    def has_add_permission(self, request: HttpRequest) -> bool:
        """Reviews are written by shoppers. One typed in here would be a fake one."""
        return False

    @admin.display(description="Author", ordering="user")
    def author(self, review: Review) -> str:
        return review.user.get_username()

    @admin.display(description="Rating", ordering="rating")
    def stars(self, review: Review) -> str:
        return "*" * review.rating

    @admin.display(description="Status", ordering="status")
    def status_badge(self, review: Review) -> Any:
        colours = {
            ReviewStatus.APPROVED: GREEN,
            ReviewStatus.PENDING: AMBER,
            ReviewStatus.REJECTED: RED,
        }
        return _swatch(colours.get(review.status, GREY), review.get_status_display())

    def _moderate(self, request: HttpRequest, queryset: QuerySet[Review], status: str) -> int:
        """Set a verdict and put every affected product's rating back in step.

        One update, then one recomputation per product touched -- rather than
        saving each review, which would recompute the same product's rating once
        per review on it.
        """
        from apps.shop.models import refresh_review_stats

        products = list(queryset.values_list("product_id", flat=True).distinct())
        changed = queryset.update(status=status)
        for product_id in products:
            refresh_review_stats(product_id)
        return changed

    @admin.action(description="Publish these reviews")
    def approve(self, request: HttpRequest, queryset: QuerySet[Review]) -> None:
        # `str()` around the member, not `.value`: a `TextChoices` member is a
        # `(value, label)` pair to a type checker, and its `__str__` is the value.
        changed = self._moderate(request, queryset, str(ReviewStatus.APPROVED))
        self.message_user(request, f"{changed} published.", messages.SUCCESS)

    @admin.action(description="Reject these reviews")
    def reject(self, request: HttpRequest, queryset: QuerySet[Review]) -> None:
        changed = self._moderate(request, queryset, str(ReviewStatus.REJECTED))
        self.message_user(request, f"{changed} rejected.", messages.WARNING)


@admin.register(ProductLike)
class ProductLikeAdmin(ReadOnlyAdmin):
    """Who liked what. A record, so nothing here is editable."""

    list_display = ("product", "user", "created_at")
    list_filter = ("created_at",)
    search_fields = ("product__name", "user__username", "user__email")
    date_hierarchy = "created_at"

    def get_queryset(self, request: HttpRequest) -> QuerySet[ProductLike]:
        return super().get_queryset(request).select_related("product", "user")


@admin.register(Address)
class AddressAdmin(ReadOnlyAdmin):
    """Where shoppers have asked for things to be sent. A support screen.

    Read-only, and for the reason carts and likes are: it is somebody's own
    data, typed in by them, and a shop that can edit a customer's address by
    hand can misdeliver a parcel and have nothing that says who did it. Orders
    are unaffected either way -- an order carries a flat copy of the address it
    was sent to, taken when it was placed.
    """

    list_display = ("full_name", "account", "city", "country", "postal_code", "default_badge")
    list_filter = ("country", "is_default")
    search_fields = (
        "full_name",
        "city",
        "postal_code",
        "line1",
        "user__username",
        "user__email",
    )

    def get_queryset(self, request: HttpRequest) -> QuerySet[Address]:
        return super().get_queryset(request).select_related("user")

    @admin.display(description="Account", ordering="user")
    def account(self, address: Address) -> str:
        return address.user.get_username()

    @admin.display(description="Default", ordering="is_default")
    def default_badge(self, address: Address) -> Any:
        return _pill(BLUE, "Default") if address.is_default else ""


@admin.register(InventoryReservation)
class InventoryReservationAdmin(ReadOnlyAdmin):
    """Why the shelf says what it says.

    Registered because "this product has three left and I know we have twelve"
    is a question with exactly one answer -- nine of them are held by orders
    that have not been paid for or cancelled yet -- and until this screen
    existed there was nowhere to read it.
    """

    list_display = ("product", "variant", "seller", "quantity", "order_link", "state")
    list_filter = (
        ("released_at", admin.EmptyFieldListFilter),
        dropdown_filter("product__category", RelatedDropdownFilter),
    )
    search_fields = ("product__name", "product__sku", "order__number")
    date_hierarchy = "created_at"

    def get_queryset(self, request: HttpRequest) -> QuerySet[InventoryReservation]:
        return (
            super()
            .get_queryset(request)
            .select_related("product", "variant", "offer__seller", "order")
        )

    @admin.display(description="Seller")
    def seller(self, reservation: InventoryReservation) -> str:
        return reservation.offer.seller.name if reservation.offer_id else "The shop"

    @admin.display(description="Order", ordering="order__number")
    def order_link(self, reservation: InventoryReservation) -> str:
        return reservation.order.number

    @admin.display(description="State", ordering="released_at")
    def state(self, reservation: InventoryReservation) -> Any:
        if reservation.released_at:
            return _swatch(GREY, f"Put back {reservation.released_at:%d %b}")
        return _swatch(AMBER, "Held")


@admin.register(OrderEvent)
class OrderEventAdmin(ReadOnlyAdmin):
    """Every step every order has taken, across the whole shop.

    The same rows the order screen shows in place, read the other way round:
    "what did we do yesterday" rather than "what happened to this order".
    """

    list_display = ("created_at", "order_number", "status_badge", "note", "actor")
    list_filter = (dropdown_filter("status", ChoicesDropdownFilter), "created_at")
    search_fields = ("order__number", "note", "actor__username")
    date_hierarchy = "created_at"

    def get_queryset(self, request: HttpRequest) -> QuerySet[OrderEvent]:
        return super().get_queryset(request).select_related("order", "actor")

    @admin.display(description="Order", ordering="order__number")
    def order_number(self, event: OrderEvent) -> str:
        return event.order.number

    @admin.display(description="Became", ordering="status")
    def status_badge(self, event: OrderEvent) -> Any:
        return _pill(ORDER_COLOURS.get(str(event.status), GREY), event.get_status_display())


@admin.register(ShippingMethod)
class ShippingMethodAdmin(ModelAdmin):
    """How orders get there, what that costs, and how long it takes."""

    list_display = ("name", "cost_summary", "speed", "is_active", "order")
    list_filter = (dropdown_filter("is_active", BooleanRadioFilter),)
    list_editable = ("order",)
    search_fields = ("name", "description")
    readonly_fields = ("id", "created_at", "updated_at")
    fieldsets = (
        (None, {"fields": ("name", "description", "is_active", "order")}),
        (
            "What it costs",
            {
                "description": (
                    "Free delivery over a threshold belongs here rather than in a "
                    "discount, because it is a property of the option and a shopper "
                    "comparing two of them needs to see it beside each."
                ),
                "fields": ("price", "free_from"),
            },
        ),
        ("How long it takes", {"fields": ("min_days", "max_days")}),
        ("Record", {"classes": ("collapse",), "fields": ("id", "created_at", "updated_at")}),
    )

    @admin.display(description="Cost", ordering="price")
    def cost_summary(self, method: ShippingMethod) -> Any:
        price = _money(method.price, options.currency())
        if method.free_from is None:
            return price
        return format_html("{} <small>(free over {})</small>", price, method.free_from)

    @admin.display(description="Takes", ordering="min_days")
    def speed(self, method: ShippingMethod) -> str:
        if method.min_days == method.max_days:
            return f"{method.min_days} day(s)"
        return f"{method.min_days}-{method.max_days} days"


@admin.register(Coupon)
class CouponAdmin(ModelAdmin):
    """The codes shoppers type in, and how much life each of them has left.

    A campaign applies by itself and can be printed on a product page; a coupon
    depends on what somebody types into a basket and cannot. That is why they
    are two screens rather than one, and why this one is mostly about limits --
    a window, a minimum, a number of uses -- which are what a shop reaches for
    when a code escapes onto a deals site.
    """

    list_display = ("code", "worth", "minimum_subtotal", "usage", "window", "state")
    list_filter = (dropdown_filter("is_active", BooleanRadioFilter),)
    search_fields = ("code",)
    readonly_fields = ("id", "used_count", "created_at", "updated_at")
    fieldsets = (
        (
            None,
            {
                "description": (
                    "A coupon is worth a percentage or an amount, never both -- "
                    "filling in both is refused rather than silently preferring one."
                ),
                "fields": ("code", "percent", "amount", "is_active"),
            },
        ),
        ("Limits", {"fields": ("minimum_subtotal", "usage_limit", "used_count")}),
        (
            "When it works",
            {
                "description": "Leave either end empty for a code with no start or no end.",
                "fields": ("starts_at", "ends_at"),
            },
        ),
        ("Record", {"classes": ("collapse",), "fields": ("id", "created_at", "updated_at")}),
    )

    @admin.display(description="Worth")
    def worth(self, coupon: Coupon) -> str:
        if coupon.percent is not None:
            return f"{coupon.percent}% off"
        return f"{_money(coupon.amount, options.currency())} off"

    @admin.display(description="Used", ordering="used_count")
    def usage(self, coupon: Coupon) -> Any:
        if coupon.usage_limit is None:
            return f"{coupon.used_count} (no limit)"
        left = coupon.usage_limit - coupon.used_count
        colour = RED if left <= 0 else (AMBER if left <= 5 else GREY)
        return _swatch(colour, f"{coupon.used_count} of {coupon.usage_limit}")

    @admin.display(description="Window")
    def window(self, coupon: Coupon) -> str:
        start = f"{coupon.starts_at:%d %b %Y}" if coupon.starts_at else "always"
        end = f"{coupon.ends_at:%d %b %Y}" if coupon.ends_at else "no end"
        return f"{start} - {end}"

    @admin.display(description="Now")
    def state(self, coupon: Coupon) -> Any:
        """Whether the code works right now, before any basket is considered.

        The same question the checkout asks, minus the minimum -- which is about
        a basket and cannot be answered on a list screen.
        """
        return _pill(GREEN, "Working") if coupon.is_running else _pill(GREY, "Not in use")


class OrderItemInline(TabularInline):
    """What was sold, at the prices that were agreed. Every field of it read-only.

    ``seller`` and ``seller_name`` are in here as well as the product's own
    columns, because on a marketplace "who sold this line" is the first question
    a refund raises -- and because a snapshot with an editable field on it is
    not a snapshot.
    """

    model = OrderItem
    extra = 0
    fields = (
        "product",
        "variant",
        "product_name",
        "seller_name",
        "sku",
        "quantity",
        "unit_price",
        "tax_rate",
        "line_total",
    )
    readonly_fields = fields
    can_delete = False

    def has_add_permission(self, request: HttpRequest, obj: Any = None) -> bool:
        """A line is written by a checkout. One typed in here would be a lie."""
        return False


class OrderEventInline(TabularInline):
    """Everything that has happened to this order, oldest first.

    The screen's centre of gravity. An order's status is one word and the
    question somebody in support is actually answering is "what happened to it,
    when, and who did that" -- which is four columns and no amount of staring at
    a status field.
    """

    model = OrderEvent
    extra = 0
    fields = ("created_at", "status", "note", "actor")
    readonly_fields = fields
    ordering = ("created_at",)
    can_delete = False
    verbose_name_plural = "History"

    def has_add_permission(self, request: HttpRequest, obj: Any = None) -> bool:
        return False

    def get_queryset(self, request: HttpRequest) -> QuerySet[OrderEvent]:
        return super().get_queryset(request).select_related("actor")


class PaymentInline(TabularInline):
    """Every attempt to collect this order, including the ones that failed.

    On the order because that is where somebody asks it. A declined card
    followed by a successful one is two rows, and an order screen showing only
    the second cannot answer why the customer is on the phone.
    """

    model = Payment
    extra = 0
    fields = ("created_at", "provider", "status", "amount", "provider_reference", "paid_at")
    readonly_fields = fields
    can_delete = False
    verbose_name_plural = "Payment attempts"

    def has_add_permission(self, request: HttpRequest, obj: Any = None) -> bool:
        return False


class ReservationInline(TabularInline):
    """The stock this order took off the shelf, and whether it went back.

    Shown because "why is this product out of stock" is answered here and
    nowhere else: a reservation with no ``released_at`` is stock a pending order
    is holding, and a shopkeeper looking at an empty shelf deserves to be told
    that rather than left to work it out.
    """

    model = InventoryReservation
    extra = 0
    fields = ("product", "variant", "offer", "quantity", "released_at")
    readonly_fields = fields
    can_delete = False
    verbose_name_plural = "Stock held"

    def has_add_permission(self, request: HttpRequest, obj: Any = None) -> bool:
        return False

    def get_queryset(self, request: HttpRequest) -> QuerySet[InventoryReservation]:
        return super().get_queryset(request).select_related("product", "variant", "offer__seller")


class OrderAttentionFilter(admin.SimpleListFilter):
    """The orders somebody has to do something about, which is not "all of them".

    A shop's order list is mostly finished orders. What a morning starts with is
    the three that are not: money that has not arrived, parcels that have not
    gone out, and things that were paid for days ago and are still sitting here.
    """

    title = "Needs attention"
    parameter_name = "attention"

    def lookups(self, request: HttpRequest, model_admin: Any) -> list[tuple[str, str]]:
        return [
            ("unpaid", "Awaiting payment"),
            ("to_send", "Paid, not yet sent"),
            ("stalled", "Paid over 3 days ago, not yet sent"),
        ]

    def queryset(self, request: HttpRequest, queryset: QuerySet[Any]) -> QuerySet[Any]:
        chosen = self.value()
        waiting = (OrderStatus.PAID, OrderStatus.PROCESSING)
        if chosen == "unpaid":
            return queryset.filter(status=OrderStatus.PENDING)
        if chosen == "to_send":
            return queryset.filter(status__in=waiting)
        if chosen == "stalled":
            return queryset.filter(
                status__in=waiting, updated_at__lt=timezone.now() - timedelta(days=3)
            )
        return queryset


@admin.register(Order)
class OrderAdmin(ModelAdmin):
    """One order, everything that has happened to it, and the steps it can take next.

    The status is **not** an editable field, and that is the whole design of this
    screen. Three of an order's steps are not a word in a column: cancelling and
    refunding put stock back on the shelf through the reservations a checkout
    wrote, and refunding closes the payments that collected the money. A status
    somebody could type over would let the column and the shelf disagree, and
    the shelf is the one customers find out about. So every move is an action,
    every action goes through the same service method a payment gateway's
    callback would, and the legal moves are
    :data:`~apps.shop.models.ORDER_TRANSITIONS` rather than a dropdown.

    Three things stay editable, because they are facts somebody types in rather
    than consequences: who is carrying the parcel, its tracking number, and the
    note. Filling those in and then running "Mark as sent" is the dispatch
    workflow, and doing it in that order is why the action does not need a form
    of its own.
    """

    list_display = (
        "number",
        "customer",
        "status_badge",
        "line_count",
        "money",
        "fulfilment",
        "invoice_number",
        "created_at",
    )
    list_filter = (
        dropdown_filter("status", ChoicesDropdownFilter),
        OrderAttentionFilter,
        "currency",
        "created_at",
        dropdown_filter("shipping_method", RelatedDropdownFilter),
    )
    search_fields = (
        "number",
        "user__email",
        "user__username",
        "tracking_number",
        "items__product_name",
        "items__sku",
    )
    date_hierarchy = "created_at"
    inlines = (OrderItemInline, PaymentInline, ReservationInline, OrderEventInline)
    list_filter_submit = True
    readonly_fields = (
        "id",
        "number",
        "user",
        "status_badge",
        "next_steps",
        "currency",
        "delivery_address",
        "shipping_method",
        "coupon",
        "subtotal",
        "coupon_discount",
        "shipping_total",
        "tax_total",
        "total",
        "collected",
        "shipped_at",
        "created_at",
        "updated_at",
    )
    fieldsets = (
        (
            None,
            {
                "description": (
                    "An order is a record of an agreement. Every price on it was "
                    "copied down when it was placed and nothing recomputes them."
                ),
                "fields": ("number", "user", "status_badge", "next_steps", "created_at"),
            },
        ),
        (
            "Delivery",
            {
                "description": (
                    "Fill the carrier and the tracking number in, save, then run "
                    "\u201cMark as sent\u201d -- the action posts whatever is here."
                ),
                "fields": (
                    "delivery_address",
                    "shipping_method",
                    "carrier",
                    "tracking_number",
                    "tracking_url",
                    "shipped_at",
                ),
            },
        ),
        (
            "Money",
            {
                "fields": (
                    "currency",
                    "subtotal",
                    "coupon",
                    "coupon_discount",
                    "shipping_total",
                    "tax_total",
                    "total",
                    "collected",
                ),
            },
        ),
        ("Note", {"fields": ("note",)}),
        ("Record", {"classes": ("collapse",), "fields": ("id", "updated_at")}),
    )

    actions = (
        "mark_paid",
        "start_processing",
        "mark_sent",
        "mark_completed",
        "cancel_orders",
        "refund_orders",
    )

    def get_queryset(self, request: HttpRequest) -> QuerySet[Order]:
        return (
            super()
            .get_queryset(request)
            .select_related("user", "shipping_method", "invoice", "coupon")
            .prefetch_related("payments")
            .annotate(lines=Count("items", distinct=True))
        )

    def has_add_permission(self, request: HttpRequest) -> bool:
        """An order is placed by a shopper going through a checkout.

        One typed in here would have no reservation behind it and no payment to
        settle, which is to say it would be a row that looks like an order and
        behaves like nothing.
        """
        return False

    @admin.display(description="Customer", ordering="user")
    def customer(self, order: Order) -> str:
        return order.user.get_username()

    @admin.display(description="Lines", ordering="lines")
    def line_count(self, order: Order) -> int:
        return getattr(order, "lines", 0)

    @admin.display(description="Status", ordering="status")
    def status_badge(self, order: Order) -> Any:
        return _pill(ORDER_COLOURS.get(str(order.status), GREY), order.get_status_display())

    @admin.display(description="Total", ordering="total")
    def money(self, order: Order) -> Any:
        """What it came to, and whether the money has actually arrived."""
        collected = order.paid_amount
        if collected >= order.total:
            return _swatch(GREEN, _money(order.total, order.currency))
        if collected:
            return _swatch(AMBER, f"{collected} of {_money(order.total, order.currency)}")
        return _swatch(GREY, _money(order.total, order.currency))

    @admin.display(description="Collected")
    def collected(self, order: Order) -> str:
        """The sum of the payments that actually succeeded, not what was asked for."""
        return _money(order.paid_amount, order.currency)

    @admin.display(description="Delivery")
    def fulfilment(self, order: Order) -> Any:
        if order.shipped_at:
            carried = f" via {order.carrier}" if order.carrier else ""
            return _swatch(GREEN, f"Sent {order.shipped_at:%d %b}{carried}")
        if str(order.status) in {OrderStatus.PAID, OrderStatus.PROCESSING}:
            return _swatch(AMBER, "Waiting to go out")
        return _swatch(GREY, "-")

    @admin.display(description="Where it can go next")
    def next_steps(self, order: Order) -> Any:
        """The legal moves from here, spelled out beside the buttons that make them.

        Written on the form because an admin who has just been refused a step
        should be able to see why without reading the source: an order is not
        cancellable once it has shipped, and this is where that is said.
        """
        if not order.pk:
            return "-"
        steps = order.next_statuses
        if not steps:
            return _swatch(GREY, "Nowhere -- this order is finished.")
        return format_html_join(
            ", ",
            "{}",
            ((OrderStatus(step).label,) for step in steps),
        )

    @admin.display(description="Sent to")
    def delivery_address(self, order: Order) -> Any:
        """The address copied onto the order, not the one in the shopper's book.

        They are different things on purpose: editing an address must not
        rewrite a parcel that has already gone out. Printed as lines rather than
        as the raw JSON the column holds, because somebody is reading it to
        write on a label.
        """
        address = order.shipping_address or {}
        if not address:
            return "-"
        lines = [
            address.get("full_name", ""),
            address.get("line1", ""),
            address.get("line2", ""),
            address.get("city", ""),
            address.get("province", ""),
            address.get("postal_code", ""),
            address.get("country", ""),
            address.get("phone", ""),
        ]
        return format_html_join("", "{}<br>", ((line,) for line in lines if line))

    @admin.display(description="Invoice")
    def invoice_number(self, order: Order) -> str:
        return getattr(getattr(order, "invoice", None), "number", "-")

    @admin.action(description="Mark as paid")
    def mark_paid(self, request: HttpRequest, queryset: QuerySet[Order]) -> None:
        """Settle the payment, and turn the stock this order reserved into a sale."""
        _apply_transition(self, request, queryset, shop_service.settle_order, "marked paid")

    @admin.action(description="Start picking")
    def start_processing(self, request: HttpRequest, queryset: QuerySet[Order]) -> None:
        _apply_transition(
            self, request, queryset, shop_service.start_processing, "now being picked"
        )

    @admin.action(description="Mark as sent, with whatever tracking is on the order")
    def mark_sent(self, request: HttpRequest, queryset: QuerySet[Order]) -> None:
        """Dispatch, using the carrier and tracking number already saved on each order.

        The action takes no form of its own on purpose: the tracking number
        arrives one order at a time from whoever printed the label, and it is
        typed into the order it belongs to. An order sent without one is a
        legitimate thing -- a shop that hands parcels over a counter has no
        number to give -- so this does not insist on one.
        """
        done, refused = 0, []
        for order in queryset:
            try:
                shop_service.ship_order(
                    order,
                    carrier=order.carrier,
                    tracking_number=order.tracking_number,
                    tracking_url=order.tracking_url,
                    actor=request.user,
                )
            except ShopRefused as refusal:
                refused.append(f"{order.number}: {refusal}")
            else:
                done += 1
        if done:
            self.message_user(request, f"{done} marked as sent.", messages.SUCCESS)
        for problem in refused:
            self.message_user(request, problem, messages.ERROR)

    @admin.action(description="Mark as delivered")
    def mark_completed(self, request: HttpRequest, queryset: QuerySet[Order]) -> None:
        _apply_transition(self, request, queryset, shop_service.complete_order, "completed")

    @admin.action(description="Cancel, and put the stock back")
    def cancel_orders(self, request: HttpRequest, queryset: QuerySet[Order]) -> None:
        """For an order nobody paid for. A paid one is refunded instead."""
        done, refused = 0, []
        for order in queryset.select_related("user"):
            try:
                shop_service.advance_order(
                    order,
                    str(OrderStatus.CANCELLED),
                    actor=request.user,
                    note="Cancelled in the admin.",
                )
            except ShopRefused as refusal:
                refused.append(f"{order.number}: {refusal}")
            else:
                done += 1
        if done:
            self.message_user(request, f"{done} cancelled.", messages.WARNING)
        for problem in refused:
            self.message_user(request, problem, messages.ERROR)

    @admin.action(description="Refund, and put the stock back")
    def refund_orders(self, request: HttpRequest, queryset: QuerySet[Order]) -> None:
        """The other end of a sale: the money goes back and so does the stock.

        Distinct from cancelling, which is for an order that was never paid for.
        Refunding also takes the units back out of each product's sales count,
        so a bestsellers list is not led by something that was all returned.
        """
        _apply_transition(
            self, request, queryset, shop_service.refund_order, "refunded", messages.WARNING
        )


@admin.register(Payment)
class PaymentAdmin(ReadOnlyAdmin):
    """Where a payment is settled, until a gateway is wired up to do it.

    The fields stay read-only -- nobody types over an amount -- and the two
    verdicts are actions, because each of them has consequences beyond a status.
    Taking a payment turns the order's reservations into sales; refusing one
    opens a fresh attempt so the shopper can try again, and deliberately leaves
    the stock reserved, since a declined card is one attempt rather than an
    abandoned order.
    """

    list_display = (
        "order",
        "provider",
        "status_badge",
        "amount",
        "currency",
        "provider_reference",
        "paid_at",
    )
    list_filter = ("provider", dropdown_filter("status", ChoicesDropdownFilter), "currency")
    search_fields = ("order__number", "provider_reference", "order__user__email")
    date_hierarchy = "created_at"
    actions = ("mark_paid", "mark_rejected")

    def get_queryset(self, request: HttpRequest) -> QuerySet[Payment]:
        return super().get_queryset(request).select_related("order__user")

    @admin.display(description="Status", ordering="status")
    def status_badge(self, payment: Payment) -> Any:
        colours = {
            PaymentStatus.SUCCEEDED: GREEN,
            PaymentStatus.PENDING: AMBER,
            PaymentStatus.FAILED: RED,
            PaymentStatus.REFUNDED: GREY,
        }
        return _swatch(colours.get(payment.status, GREY), payment.get_status_display())

    @admin.action(description="Mark as paid")
    def mark_paid(self, request: HttpRequest, queryset: QuerySet[Payment]) -> None:
        """Settle each selected payment's order, and count the sale.

        Per row rather than as one update: settling is a transaction that also
        moves the order and the products' sales counts, and it is idempotent, so
        a row somebody selected twice costs nothing.
        """
        self._apply(request, queryset, shop_service.settle_order, "taken", messages.SUCCESS)

    @admin.action(description="Mark as rejected")
    def mark_rejected(self, request: HttpRequest, queryset: QuerySet[Payment]) -> None:
        """Record that the money did not arrive, and open a fresh attempt."""
        self._apply(request, queryset, shop_service.reject_payment, "refused", messages.WARNING)

    def _apply(
        self,
        request: HttpRequest,
        queryset: QuerySet[Payment],
        verdict: Any,
        past_tense: str,
        level: int,
    ) -> None:
        """Run one verdict over the selected payments and report both counts.

        A refusal from the service -- an order already paid, a payment already
        settled -- is reported rather than raised: an admin who selected fifteen
        rows wants the twelve that worked to have worked.
        """
        done, refused = 0, []
        for payment in queryset.select_related("order"):
            try:
                verdict(payment.order)
            except ShopRefused as refusal:
                refused.append(f"{payment.order.number}: {refusal}")
            else:
                done += 1
        if done:
            self.message_user(request, f"{done} {past_tense}.", level)
        for problem in refused:
            self.message_user(request, problem, messages.ERROR)


@admin.register(Invoice)
class InvoiceAdmin(ModelAdmin):
    """The issued documents. The numbers on them belong to the order.

    Only the two things a shop actually edits after issuing are editable -- when
    payment is due, and whatever has to be printed on it. Everything a client
    would read off it is the order's, and is shown here rather than copied.
    """

    list_display = ("number", "order", "customer", "total", "order_status", "issued_at")
    list_filter = ("issued_at", "order__status")
    search_fields = ("number", "order__number", "order__user__email", "order__user__username")
    date_hierarchy = "issued_at"
    readonly_fields = ("id", "number", "order", "issued_at", "created_at", "updated_at")
    fields = ("number", "order", "issued_at", "due_at", "notes", "id", "created_at", "updated_at")

    def get_queryset(self, request: HttpRequest) -> QuerySet[Invoice]:
        return super().get_queryset(request).select_related("order__user")

    def has_add_permission(self, request: HttpRequest) -> bool:
        """An invoice is issued by a checkout, never typed in."""
        return False

    @admin.display(description="Customer")
    def customer(self, invoice: Invoice) -> str:
        return invoice.order.user.get_username()

    @admin.display(description="Total", ordering="order__total")
    def total(self, invoice: Invoice) -> str:
        return f"{invoice.order.total} {invoice.order.currency}"

    @admin.display(description="Order", ordering="order__status")
    def order_status(self, invoice: Invoice) -> Any:
        colours = {
            OrderStatus.PAID: GREEN,
            OrderStatus.PENDING: AMBER,
            OrderStatus.CANCELLED: RED,
        }
        return _swatch(colours.get(invoice.order.status, GREY), invoice.order.get_status_display())
