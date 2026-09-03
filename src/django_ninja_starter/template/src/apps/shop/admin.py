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

from typing import Any

from django.contrib import admin, messages
from django.db.models import Count, Prefetch, QuerySet
from django.http import HttpRequest
from django.utils.html import format_html

from apps.shop import options
from apps.shop.models import (
    Brand,
    Cart,
    CartItem,
    Category,
    CategoryAttribute,
    Collection,
    CollectionItem,
    Coupon,
    Discount,
    Invoice,
    Order,
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


def _swatch(colour: str, text: str) -> Any:
    return format_html('<span style="color: {}; font-weight: 600">{}</span>', colour, text)


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


class StockFilter(admin.SimpleListFilter):
    """The question a shopkeeper asks first thing in the morning."""

    title = "Stock"
    parameter_name = "stock_state"

    def lookups(self, request: HttpRequest, model_admin: Any) -> list[tuple[str, str]]:
        return [("out", "Out of stock"), ("low", "Running low"), ("in", "In stock")]

    def queryset(self, request: HttpRequest, queryset: QuerySet[Any]) -> QuerySet[Any]:
        if self.value() == "out":
            return queryset.filter(track_inventory=True, has_variants=False, stock__lte=0)
        if self.value() == "low":
            return queryset.filter(
                track_inventory=True, has_variants=False, stock__gt=0, stock__lte=10
            )
        if self.value() == "in":
            return queryset.in_stock()
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
    model = CartItem
    extra = 0
    fields = ("product", "variant", "quantity", "created_at")
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


@admin.register(ShippingMethod)
class ShippingMethodAdmin(ModelAdmin):
    list_display = ("name", "price", "free_from", "min_days", "max_days", "is_active", "order")
    list_editable = ("order", "is_active")


@admin.register(Coupon)
class CouponAdmin(ModelAdmin):
    list_display = (
        "code",
        "percent",
        "amount",
        "minimum_subtotal",
        "used_count",
        "usage_limit",
        "is_active",
    )
    list_filter = ("is_active",)
    search_fields = ("code",)


class OrderItemInline(TabularInline):
    model = OrderItem
    extra = 0
    readonly_fields = (
        "product",
        "variant",
        "product_name",
        "sku",
        "quantity",
        "unit_price",
        "tax_rate",
        "line_total",
    )
    can_delete = False


@admin.register(Order)
class OrderAdmin(ModelAdmin):
    list_display = (
        "number",
        "user",
        "status",
        "total",
        "currency",
        "invoice_number",
        "created_at",
    )
    list_filter = (dropdown_filter("status", ChoicesDropdownFilter), "currency")
    search_fields = ("number", "user__email", "user__username")
    inlines = (OrderItemInline,)
    readonly_fields = (
        "id",
        "number",
        "user",
        "currency",
        "shipping_address",
        "shipping_method",
        "coupon",
        "subtotal",
        "coupon_discount",
        "shipping_total",
        "tax_total",
        "total",
        "note",
        "created_at",
        "updated_at",
    )

    actions = ("mark_paid", "cancel_orders")

    def get_queryset(self, request: HttpRequest) -> QuerySet[Order]:
        return (
            super()
            .get_queryset(request)
            .select_related("user", "shipping_method", "invoice")
            .prefetch_related("payments")
        )

    @admin.display(description="Invoice")
    def invoice_number(self, order: Order) -> str:
        return getattr(getattr(order, "invoice", None), "number", "-")

    @admin.action(description="Mark as paid")
    def mark_paid(self, request: HttpRequest, queryset: QuerySet[Order]) -> None:
        """The same settling the payment screen does, from the order's side."""
        done, refused = 0, []
        for order in queryset:
            try:
                shop_service.settle_order(order)
            except ShopRefused as refusal:
                refused.append(f"{order.number}: {refusal}")
            else:
                done += 1
        if done:
            self.message_user(request, f"{done} marked paid.", messages.SUCCESS)
        for problem in refused:
            self.message_user(request, problem, messages.ERROR)

    @admin.action(description="Cancel and release the stock")
    def cancel_orders(self, request: HttpRequest, queryset: QuerySet[Order]) -> None:
        """The other end of a refused payment: the order is done, so the stock
        goes back on the shelf."""
        done, refused = 0, []
        for order in queryset.select_related("user"):
            try:
                shop_service.cancel_order(order.user, order.number)
            except ShopRefused as refusal:
                refused.append(f"{order.number}: {refusal}")
            else:
                done += 1
        if done:
            self.message_user(request, f"{done} cancelled.", messages.WARNING)
        for problem in refused:
            self.message_user(request, problem, messages.ERROR)


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
