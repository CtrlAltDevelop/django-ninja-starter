"""The shop: a catalogue somebody curates, and what shoppers do to it.

This app is meant to be lifted out and dropped into any Django project, so it
owns everything it needs and asks the project for almost nothing: settings it
defaults for itself, a router mount, and the account model Django already has.
Nothing here imports the project around it.

There are two halves, and they are governed differently.

The **catalogue** -- brands, categories, the attributes a category declares,
products, their images, their variants, curated collections and the discounts
that run on them -- is written in the admin and nowhere else. There is no
endpoint that creates a product. A shop's catalogue is its balance sheet, and an
API that lets a client write to it needs an authorisation model this app does
not have and most shops do not want. So the API reads, and the admin writes.

The **shopper's half** -- a cart, a review, a like -- is the opposite: written
over the API by the account it belongs to, and by nobody else. Every queryset in
that half starts from the caller, so there is no argument that can widen it to
somebody else's basket.

Six decisions are worth stating, because each of them replaces something that
looks simpler and is not.

**A category is the shape of a product, not a folder.** ``CategoryAttribute``
rows hang off a category and declare what its products have: screen size in
inches as a required number, colour as one of a list. A product fills them in,
validated against the declared type. The alternative -- a wide products table
with forty nullable columns, or an untyped bag of JSON -- means either a
migration every time the shop starts selling a new kind of thing, or a
storefront defending against every shape at render time. Attributes are
inherited down the tree, so "brand warranty, in months" declared on Electronics
is answered by every laptop underneath it.

**A discount is a row with a window, not a column on the product.** The obvious
design is ``sale_price`` and ``sale_ends_at`` on the product, and it breaks the
first time a shop runs "20% off everything in Kitchen this weekend": somebody
writes a loop over nine hundred products, and a second loop to put them back,
and the second loop is the one that gets interrupted. Here the campaign is one
row naming what it applies to and when it runs, the product's own price is never
overwritten, and the sale ends because a clock passed a timestamp rather than
because a script ran.

**Stock lives wherever the thing being sold does.** A product with no variants
carries its own stock. A product with variants carries none, and each variant
carries its own -- because "3 left" is meaningless for a shirt that exists in
four sizes. The model refuses the halfway state instead of leaving two numbers
that disagree.

**A cart stores quantities, not prices.** Snapshotting the price at add-time
looks like protecting the shopper and is really a way to sell yesterday's price
next month, and to hold a basket at a discount that ended. The cart is a list of
intentions; the price is computed when it is read, from the same code the
product page used.

**Ratings are cached, and the reviews are the truth.** ``rating_average`` and
``rating_count`` are columns because "products rated four and up, best first" is
a query a storefront makes constantly and an aggregate over every review is not
a thing to do per row. They are recomputed from the reviews on every change, in
one place, so the cache cannot drift by being updated in five.

**Moderation is a state, not a deletion.** A rejected review stays, with a
reason, because the shopper who wrote it and the moderator who rejected it are
two different people having a disagreement, and deleting one side of it makes
the shop unable to answer what happened.

**An order is a snapshot, and the cart is not.** Everything above is computed
when it is read, because the catalogue changes. An order is the opposite: the
address, the price of each line and the tax on it are copied onto it at the
moment it is placed, and nothing recomputes them afterwards. A shopper who edits
their address book must not rewrite an order that has already shipped, and a
sale ending must not change what somebody agreed to pay. Stock moves at that
same moment rather than at payment -- as a reservation that a cancellation
releases -- because two shoppers reaching a checkout page for the last one in
stock must not both succeed.
"""

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any, Self

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.serializers.json import DjangoJSONEncoder
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models, transaction
from django.db.models import Avg, Count, Q
from django.utils import timezone

from apps.shop import options
from apps.shop.attributes import (
    CHOICE_TYPES,
    VARIANT_TYPES,
    AttributeType,
    clean_choices,
    is_empty,
    normalize_value,
)

#: How deep a category tree may go. Not a technical limit -- it is the depth at
#: which a breadcrumb stops fitting on a phone and shoppers stop finding things.
MAX_CATEGORY_DEPTH = 5

#: Every price in this app. Twelve digits with two after the point holds any
#: realistic price in any currency without inviting floating point anywhere near
#: money.
MONEY = {"max_digits": 12, "decimal_places": 2}

MIN_RATING = 1
MAX_RATING = 5


class ShopModel(models.Model):
    """A stable id and the two dates every row here carries.

    UUID keys rather than sequential ones because these ids appear in URLs a
    storefront hands out, and a sequential one tells anybody who looks how many
    orders a shop has taken this month.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True

    def save(self, *args: Any, **kwargs: Any) -> None:
        """Validate every write, wherever it came from.

        The admin would do this anyway; an import, a shell session and a data
        migration would not, and those are exactly the writes nobody watches.
        Bulk counter updates go through ``queryset.update()``, which is not a
        save and deliberately skips this.
        """
        self.full_clean()
        super().save(*args, **kwargs)


class ActivatableModel(ShopModel):
    """Something an admin turns off without deleting it."""

    is_active = models.BooleanField(
        "Shown",
        default=True,
        help_text="Hidden rows stay in the admin and disappear from the API.",
    )

    class Meta:
        abstract = True


class Brand(ActivatableModel):
    """Who makes the thing. Its own row, because shoppers filter by it."""

    name = models.CharField(max_length=120, unique=True)
    slug = models.SlugField(max_length=120, unique=True, help_text="Used in URLs and filters.")
    description = models.TextField(blank=True)
    logo = models.URLField(blank=True)
    website = models.URLField(blank=True)
    order = models.PositiveIntegerField(default=0, help_text="Order in a brand list.")

    class Meta:
        verbose_name = "Brand"
        verbose_name_plural = "Brands"
        ordering = ("order", "name")

    def __str__(self) -> str:
        return self.name


class Seller(ActivatableModel):
    """A shop within the shop: whoever is actually selling the thing.

    A marketplace and a single-vendor store are the same tables here. A product
    names the seller whose price and stock the product row carries, and any
    other seller who can fulfil it gets a :class:`ProductOffer`. A product that
    names nobody is sold by the shop itself, which is what a single-vendor
    project sees and never has to think about.

    ``owner`` is the account that manages this seller, and it is nullable
    because a shop usually starts out running its own sellers from the admin.
    It is a pointer for whatever authorisation a project adds later, not an
    authorisation model this app pretends to have.
    """

    name = models.CharField(max_length=150, unique=True)
    slug = models.SlugField(max_length=150, unique=True, help_text="Used in URLs and filters.")
    description = models.TextField(blank=True)
    logo = models.URLField(blank=True)
    email = models.EmailField(blank=True)
    phone = models.CharField(max_length=40, blank=True)
    city = models.CharField(max_length=100, blank=True)
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="shop_sellers",
        help_text="The account that manages this seller, if one does.",
    )
    order = models.PositiveIntegerField(default=0, help_text="Order in a seller list.")

    class Meta:
        verbose_name = "Seller"
        verbose_name_plural = "Sellers"
        ordering = ("order", "name")

    def __str__(self) -> str:
        return self.name


class CategoryQuerySet(models.QuerySet["Category"]):
    def live(self) -> "CategoryQuerySet":
        return self.filter(is_active=True)

    def roots(self) -> "CategoryQuerySet":
        return self.filter(parent__isnull=True)


class Category(ActivatableModel):
    """A branch of the catalogue, and the shape of the products on it.

    The tree is a parent pointer rather than nested sets or a materialised path:
    a shop has tens of categories, not millions, and the operations that matter
    -- "this one and everything under it" -- are a handful of queries at that
    size. Nested sets would buy speed nobody needs at the price of every write
    touching rows it did not change.
    """

    parent = models.ForeignKey(
        "self",
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="children",
        help_text="Leave empty for a top-level category.",
    )
    name = models.CharField(max_length=150)
    slug = models.SlugField(
        max_length=150,
        unique=True,
        help_text="The name the API is asked for, for example laptops.",
    )
    description = models.TextField(blank=True)
    image = models.URLField(blank=True)
    icon = models.CharField(
        max_length=60, blank=True, help_text="An icon name a storefront knows how to draw."
    )
    order = models.PositiveIntegerField(default=0, help_text="Order among its siblings.")
    meta_title = models.CharField(max_length=200, blank=True)
    meta_description = models.CharField(max_length=300, blank=True)
    meta_keywords = models.JSONField(default=list, blank=True, help_text='["laptops", "gaming"]')

    objects = CategoryQuerySet.as_manager()

    class Meta:
        verbose_name = "Category"
        verbose_name_plural = "Categories"
        ordering = ("order", "name")
        indexes = (
            models.Index(fields=("parent", "order")),
            models.Index(fields=("is_active",)),
        )

    def __str__(self) -> str:
        return " / ".join(category.name for category in self.ancestors(including_self=True))

    def clean(self) -> None:
        super().clean()
        if not isinstance(self.meta_keywords, list) or not all(
            isinstance(word, str) for word in self.meta_keywords
        ):
            raise ValidationError({"meta_keywords": "Expected a list of words."})
        if self.parent_id is None:
            return
        if self.parent_id == self.pk:
            raise ValidationError({"parent": "A category cannot be its own parent."})
        seen = {self.pk}
        parent: Category | None = self.parent
        depth = 1
        while parent is not None:
            if parent.pk in seen:
                raise ValidationError({"parent": "That would make the category tree a loop."})
            seen.add(parent.pk)
            depth += 1
            if depth > MAX_CATEGORY_DEPTH:
                raise ValidationError(
                    {"parent": f"Categories nest {MAX_CATEGORY_DEPTH} deep, no further."}
                )
            parent = parent.parent

    def ancestors(self, *, including_self: bool = False) -> list["Category"]:
        """This category's parents, outermost first: the breadcrumb trail."""
        trail: list[Category] = [self] if including_self else []
        parent = self.parent
        while parent is not None:
            trail.append(parent)
            parent = parent.parent
        return list(reversed(trail))

    def descendants(self) -> list["Category"]:
        """Every category under this one, at any depth, excluding itself."""
        found: list[Category] = []
        frontier = list(self.children.all())
        while frontier:
            category = frontier.pop()
            found.append(category)
            frontier.extend(category.children.all())
        return found

    def branch_ids(self) -> list[uuid.UUID]:
        """This category and everything under it: what "products in Kitchen" means."""
        return [self.pk, *(category.pk for category in self.descendants())]

    def attribute_schema(self) -> list["CategoryAttribute"]:
        """Every attribute a product here answers, inherited ones included.

        Outermost first, so a spec table reads from the general to the specific.
        A child that declares the same code as an ancestor wins, which is what
        makes "Electronics says warranty is optional, Laptops says it is
        required" mean the obvious thing.
        """
        schema: dict[str, CategoryAttribute] = {}
        for category in self.ancestors(including_self=True):
            for attribute in category.attributes.all():
                schema[attribute.code] = attribute
        return sorted(schema.values(), key=lambda attribute: (attribute.order, attribute.name))


class CategoryAttribute(ShopModel):
    """One thing every product in a category has an answer for.

    ``is_variant`` is the interesting flag. An attribute marked with it is what
    tells one variant of a product from another -- size, colour -- so it is
    answered per variant rather than per product, and a product with variants
    must not answer it itself. Everything else is a fact about the product as a
    whole and is answered once.
    """

    category = models.ForeignKey(Category, on_delete=models.CASCADE, related_name="attributes")
    name = models.CharField("Label", max_length=150, help_text="Shown to shoppers in a spec table.")
    code = models.SlugField(
        max_length=80, help_text="The key a client reads, for example screen-size."
    )
    attribute_type = models.CharField(
        "Type", max_length=20, choices=AttributeType.choices, default=AttributeType.TEXT
    )
    unit = models.CharField(
        max_length=20, blank=True, help_text='Shown after the value: "in", "kg", "W".'
    )
    choices = models.JSONField(
        default=list,
        blank=True,
        help_text='The allowed values, for a choice attribute: ["S", "M", "L"].',
    )
    required = models.BooleanField(
        default=False, help_text="A product without an answer is flagged as incomplete."
    )
    is_variant = models.BooleanField(
        "Distinguishes variants",
        default=False,
        help_text="Answered per variant -- size, colour -- rather than per product.",
    )
    is_filterable = models.BooleanField(
        default=True, help_text="Offered as a filter on the category's product list."
    )
    help_text = models.CharField(max_length=300, blank=True)
    order = models.PositiveIntegerField(default=0)

    class Meta:
        verbose_name = "Category attribute"
        verbose_name_plural = "Category attributes"
        ordering = ("order", "name")
        constraints = (
            models.UniqueConstraint(
                fields=("category", "code"), name="unique_category_attribute_code"
            ),
        )
        indexes = (models.Index(fields=("category", "order")),)

    def __str__(self) -> str:
        return f"{self.category.name} - {self.name}"

    def clean(self) -> None:
        super().clean()
        self.choices = clean_choices(self.choices)
        if self.attribute_type in CHOICE_TYPES and not self.choices:
            raise ValidationError({"choices": "A choice attribute needs a list to choose from."})
        if self.attribute_type not in CHOICE_TYPES and self.choices:
            raise ValidationError(
                {"choices": "Only a choice attribute has a list. Clear this or change the type."}
            )
        if self.is_variant and self.attribute_type not in VARIANT_TYPES:
            allowed = ", ".join(sorted(str(kind) for kind in VARIANT_TYPES))
            raise ValidationError(
                {
                    "is_variant": (
                        "A shopper picks a variant from a finite list, so only these "
                        f"types can distinguish one: {allowed}."
                    )
                }
            )

    def normalize(self, value: Any) -> Any:
        """The canonical form of one answer to this attribute."""
        return normalize_value(self.attribute_type, value, choices=self.choices)


class Tag(ShopModel):
    """A free label on a product: "vegan", "refurbished", "bestseller-2026".

    Deliberately not a second category tree. A tag says nothing about the shape
    of the product, carries no attributes and imposes no hierarchy, which is
    exactly why it is the right place for the things a shop wants to group by
    this month and not next.
    """

    name = models.CharField(max_length=80, unique=True)
    slug = models.SlugField(max_length=80, unique=True)

    class Meta:
        verbose_name = "Tag"
        verbose_name_plural = "Tags"
        ordering = ("name",)

    def __str__(self) -> str:
        return self.name


class ProductStatus(models.TextChoices):
    DRAFT = "draft", "Draft"
    ACTIVE = "active", "Active"
    ARCHIVED = "archived", "Archived"


class ProductQuerySet(models.QuerySet["Product"]):
    def live(self, at: datetime | None = None) -> "ProductQuerySet":
        """Active, and not waiting for a date that has not arrived.

        The three states are not a boolean. A draft has never been sold; an
        archived product has been, and its reviews, its cart lines and whatever
        history points at it all still have to resolve. Collapsing them into
        ``is_active`` is how a shop ends up deleting the row instead.
        """
        moment = at or timezone.now()
        return self.filter(status=ProductStatus.ACTIVE).filter(
            Q(published_at__isnull=True) | Q(published_at__lte=moment)
        )

    def in_branch(self, category: Category) -> "ProductQuerySet":
        """Everything in this category and in every category under it."""
        return self.filter(category_id__in=category.branch_ids())

    def search(self, term: str) -> "ProductQuerySet":
        """Match a shopper's words against the fields they would expect.

        Case-insensitive substring matching across name, summary, description,
        SKU, brand and tag. It is not a search engine and does not pretend to
        be: no stemming, no ranking, no typo tolerance. What it is, is correct
        on every database this project supports and honest about its limits --
        a shop that outgrows it wants Postgres full-text or a real index, and
        this is the one method it has to replace to get there.
        """
        words = [word for word in term.split() if word]
        matches = self
        for word in words:
            matches = matches.filter(
                Q(name__icontains=word)
                | Q(summary__icontains=word)
                | Q(description__icontains=word)
                | Q(sku__icontains=word)
                | Q(brand__name__icontains=word)
                | Q(tags__name__icontains=word)
            )
        return matches.distinct() if words else self

    def in_stock(self) -> "ProductQuerySet":
        """What can be bought right now, from anybody selling it.

        Counts variants for products that have them, and other sellers' offers
        for products that have those: a thing the shop itself has run out of is
        still buyable while another seller has it.
        """
        return self.filter(
            Q(track_inventory=False)
            | Q(allow_backorder=True)
            | Q(has_variants=True, variants__is_active=True, variants__stock__gt=0)
            | Q(has_variants=False, stock__gt=0)
            | Q(offers__is_active=True, offers__seller__is_active=True, offers__stock__gt=0)
        ).distinct()


class Product(ShopModel):
    """One thing a shop sells.

    Wide on purpose. Every column here is a question a storefront, a spec table,
    a shipping estimate or a listing asks constantly, and the alternative to a
    column is a join or a JSON blob that nothing can index.
    """

    category = models.ForeignKey(
        Category,
        on_delete=models.PROTECT,
        related_name="products",
        help_text="Decides which attributes this product answers.",
    )
    brand = models.ForeignKey(
        Brand, null=True, blank=True, on_delete=models.SET_NULL, related_name="products"
    )
    seller = models.ForeignKey(
        Seller,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="products",
        help_text=(
            "Who sells it at the price and stock on this page. Leave empty for the "
            "shop itself. Other sellers are added as offers."
        ),
    )
    tags = models.ManyToManyField(Tag, blank=True, related_name="products")

    name = models.CharField(max_length=250)
    slug = models.SlugField(max_length=250, unique=True, help_text="The name the API is asked for.")
    subtitle = models.CharField(max_length=250, blank=True, help_text="One line under the name.")
    summary = models.TextField(blank=True, help_text="A paragraph, for cards and search results.")
    description = models.TextField(blank=True, help_text="The full description, for the page.")

    sku = models.CharField(
        "SKU", max_length=64, unique=True, help_text="The shop's own code for this product."
    )
    barcode = models.CharField(max_length=64, blank=True, help_text="EAN, UPC or ISBN.")

    price = models.DecimalField(
        **MONEY, validators=[MinValueValidator(Decimal("0"))], help_text="Before any discount."
    )
    compare_at_price = models.DecimalField(
        **MONEY,
        null=True,
        blank=True,
        validators=[MinValueValidator(Decimal("0"))],
        help_text="The higher price to show struck through. Not a discount -- those have dates.",
    )
    cost_price = models.DecimalField(
        **MONEY,
        null=True,
        blank=True,
        validators=[MinValueValidator(Decimal("0"))],
        help_text="What it cost the shop. Never published.",
    )
    tax_rate = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        default=Decimal("0"),
        validators=[MinValueValidator(Decimal("0")), MaxValueValidator(Decimal("100"))],
        help_text="Percent. Quoted alongside the price; this app does not add it on.",
    )

    status = models.CharField(
        max_length=16,
        choices=ProductStatus.choices,
        default=ProductStatus.DRAFT,
        help_text="A draft is invisible to the API. An archived product stays readable by id.",
    )
    published_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="Leave empty to appear as soon as it is active, or set a date to wait for.",
    )
    is_featured = models.BooleanField(
        default=False, help_text="Appears in the featured listing, wherever a storefront puts it."
    )

    has_variants = models.BooleanField(
        default=False,
        help_text="This product is sold in variants. Stock and any price override live on them.",
    )
    track_inventory = models.BooleanField(
        default=True, help_text="Turn off for something that cannot run out, like a download."
    )
    stock = models.IntegerField(
        default=0, help_text="Ignored when this product has variants -- they carry their own."
    )
    low_stock_threshold = models.PositiveIntegerField(
        default=5, help_text="At or below this, the admin says so."
    )
    allow_backorder = models.BooleanField(
        default=False, help_text="Let it be added to a cart with nothing in stock."
    )

    weight_grams = models.PositiveIntegerField(null=True, blank=True)
    length_mm = models.PositiveIntegerField(null=True, blank=True)
    width_mm = models.PositiveIntegerField(null=True, blank=True)
    height_mm = models.PositiveIntegerField(null=True, blank=True)

    meta_title = models.CharField(max_length=200, blank=True)
    meta_description = models.CharField(max_length=300, blank=True)
    meta_keywords = models.JSONField(default=list, blank=True)

    # Caches. Every one of them is derived from rows elsewhere, and every one of
    # them is recomputed in exactly one function, below. They are columns because
    # "four stars and up, most liked first" is an ordering, and an ordering
    # cannot be a Python property.
    rating_average = models.DecimalField(
        max_digits=3, decimal_places=2, default=Decimal("0"), editable=False
    )
    rating_count = models.PositiveIntegerField(default=0, editable=False)
    like_count = models.PositiveIntegerField(default=0, editable=False)
    sales_count = models.PositiveIntegerField(
        default=0, help_text="Units sold. Maintained by whatever takes the orders."
    )
    view_count = models.PositiveIntegerField(default=0, editable=False)
    order = models.PositiveIntegerField(default=0, help_text="Order in a hand-sorted list.")

    objects = ProductQuerySet.as_manager()

    class Meta:
        verbose_name = "Product"
        verbose_name_plural = "Products"
        ordering = ("order", "-created_at")
        indexes = (
            models.Index(fields=("status", "published_at")),
            models.Index(fields=("category", "status")),
            models.Index(fields=("brand", "status")),
            models.Index(fields=("-sales_count",)),
            models.Index(fields=("-rating_average", "-rating_count")),
            models.Index(fields=("price",)),
        )
        constraints = (
            models.CheckConstraint(
                condition=Q(price__gte=Decimal("0")),
                name="product_price_not_negative",
                violation_error_message="A price cannot be negative.",
            ),
        )

    def __str__(self) -> str:
        return self.name

    def clean(self) -> None:
        super().clean()
        if not isinstance(self.meta_keywords, list) or not all(
            isinstance(word, str) for word in self.meta_keywords
        ):
            raise ValidationError({"meta_keywords": "Expected a list of words."})
        if self.compare_at_price is not None and self.compare_at_price <= self.price:
            raise ValidationError(
                {
                    "compare_at_price": (
                        "The struck-through price has to be higher than the price. "
                        "For a real sale, add a discount instead -- it has dates."
                    )
                }
            )
        if self.has_variants and self.stock:
            raise ValidationError(
                {
                    "stock": (
                        "This product is sold in variants, and each of them carries its "
                        "own stock. Leave this at zero."
                    )
                }
            )

    @property
    def is_live(self) -> bool:
        """Whether a shopper asking for this product right now would be shown it."""
        if self.status != ProductStatus.ACTIVE:
            return False
        return self.published_at is None or self.published_at <= timezone.now()

    @property
    def available_stock(self) -> int:
        """How many can be bought, counting variants for a product that has them."""
        if not self.track_inventory:
            return 0
        if self.has_variants:
            return sum(variant.stock for variant in self.variants.all() if variant.is_active)
        return self.stock

    @property
    def in_stock(self) -> bool:
        """Whether anybody selling this could fill an order for one."""
        if not self.track_inventory or self.allow_backorder:
            return True
        if self.available_stock > 0:
            return True
        return any(
            offer.stock > 0
            for offer in self.offers.all()
            if offer.is_active and offer.seller.is_active
        )

    @property
    def is_low_stock(self) -> bool:
        return self.track_inventory and 0 < self.available_stock <= self.low_stock_threshold

    def missing_attributes(self) -> list[CategoryAttribute]:
        """The required attributes nobody has filled in yet.

        A question rather than a constraint, for the same reason the CMS asks it
        rather than enforcing it: the schema is designed before anybody has
        typed a word, and a required attribute that made saving impossible could
        never be declared in the first place. The admin asks it where somebody
        is in a position to answer.
        """
        answered = {value.attribute_id for value in self.attribute_values.all()}
        return [
            attribute
            for attribute in self.category.attribute_schema()
            if attribute.required and not attribute.is_variant and attribute.pk not in answered
        ]


class ProductImage(ShopModel):
    """One picture of one product.

    URLs rather than uploaded files: a shop's images belong on a CDN or in
    object storage, and an app that owns an ``ImageField`` owns a media root, a
    storage backend and a thumbnailing decision that the project around it
    almost certainly has opinions about.
    """

    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name="images")
    url = models.URLField(max_length=500)
    alt = models.CharField(
        max_length=250, blank=True, help_text="What the picture shows, for a screen reader."
    )
    caption = models.CharField(max_length=250, blank=True)
    is_primary = models.BooleanField(
        default=False, help_text="The one shown on cards and in search results."
    )
    order = models.PositiveIntegerField(default=0)

    class Meta:
        verbose_name = "Product image"
        verbose_name_plural = "Product images"
        ordering = ("-is_primary", "order")
        constraints = (
            models.UniqueConstraint(
                fields=("product",),
                condition=Q(is_primary=True),
                name="one_primary_image_per_product",
                violation_error_message="This product already has a main image.",
            ),
        )
        indexes = (models.Index(fields=("product", "order")),)

    def __str__(self) -> str:
        return self.alt or self.url


class ProductVariant(ActivatableModel):
    """One buyable version of a product: this size, in this colour.

    ``options`` is a ``{code: value}`` object rather than a table of rows, and
    that is the one place this app stores structure in JSON rather than in
    columns. The reason is that the set of options is decided by the category,
    not by this table, and the operation that matters -- "the shopper picked
    red and 42, which variant is that?" -- is an exact match on the whole
    object. Rows would make it a join per option and a ``GROUP BY`` to check
    that no extra ones matched.
    """

    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name="variants")
    name = models.CharField(
        max_length=200, blank=True, help_text="Shown in a picker. Built from the options if empty."
    )
    sku = models.CharField("SKU", max_length=64, unique=True)
    options = models.JSONField(
        default=dict,
        encoder=DjangoJSONEncoder,
        help_text='The variant attributes this one answers: {"colour": "Red"}.',
    )
    price = models.DecimalField(
        **MONEY,
        null=True,
        blank=True,
        validators=[MinValueValidator(Decimal("0"))],
        help_text="Overrides the product's price. Leave empty to charge the same.",
    )
    stock = models.IntegerField(default=0)
    image = models.URLField(max_length=500, blank=True)
    order = models.PositiveIntegerField(default=0)

    class Meta:
        verbose_name = "Variant"
        verbose_name_plural = "Variants"
        ordering = ("order", "sku")
        indexes = (models.Index(fields=("product", "order")),)

    def __str__(self) -> str:
        return self.label

    @property
    def label(self) -> str:
        """What a picker shows: the given name, or the options spelled out."""
        if self.name:
            return self.name
        spelled = ", ".join(f"{key}: {value}" for key, value in sorted(self.options.items()))
        return spelled or self.sku

    def clean(self) -> None:
        super().clean()
        if not isinstance(self.options, dict):
            raise ValidationError({"options": "Expected an object of attribute codes to values."})
        if not self.product_id:
            return
        schema = {
            attribute.code: attribute
            for attribute in self.product.category.attribute_schema()
            if attribute.is_variant
        }
        if not schema:
            raise ValidationError(
                {
                    "options": (
                        "This product's category declares no variant attributes, so "
                        "there is nothing to tell one variant from another. Mark an "
                        "attribute as distinguishing variants first."
                    )
                }
            )
        unknown = sorted(set(self.options) - set(schema))
        if unknown:
            raise ValidationError(
                {
                    "options": (
                        f"Not a variant attribute of this category: {', '.join(unknown)}. "
                        f"It has {', '.join(sorted(schema))}."
                    )
                }
            )
        missing = sorted(set(schema) - set(self.options))
        if missing:
            raise ValidationError(
                {"options": f"Every variant has to answer: {', '.join(missing)}."}
            )
        cleaned: dict[str, Any] = {}
        for code, value in self.options.items():
            try:
                cleaned[code] = schema[code].normalize(value)
            except ValidationError as error:
                raise ValidationError(
                    {"options": f"{code}: {'; '.join(error.messages)}"}
                ) from error
        self.options = cleaned
        clash = (
            ProductVariant.objects.filter(product_id=self.product_id, options=cleaned)
            .exclude(pk=self.pk)
            .exists()
        )
        if clash:
            raise ValidationError({"options": "Another variant of this product is that one."})


class ProductAttribute(ShopModel):
    """One product's answer to one of its category's attributes."""

    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name="attribute_values")
    attribute = models.ForeignKey(
        CategoryAttribute, on_delete=models.CASCADE, related_name="values"
    )
    value = models.JSONField(encoder=DjangoJSONEncoder)

    class Meta:
        verbose_name = "Product attribute"
        verbose_name_plural = "Product attributes"
        ordering = ("attribute__order", "attribute__name")
        constraints = (
            models.UniqueConstraint(
                fields=("product", "attribute"),
                name="unique_product_attribute",
                violation_error_message="This product already answers that attribute.",
            ),
        )

    def __str__(self) -> str:
        return f"{self.product.name} - {self.attribute.name}"

    def clean(self) -> None:
        super().clean()
        if not self.product_id or not self.attribute_id:
            return
        attribute = self.attribute
        if attribute.is_variant:
            raise ValidationError(
                {
                    "attribute": (
                        f"{attribute.name} distinguishes variants, so each variant "
                        "answers it rather than the product."
                    )
                }
            )
        allowed = {declared.pk for declared in self.product.category.attribute_schema()}
        if attribute.pk not in allowed:
            raise ValidationError(
                {
                    "attribute": (
                        f"{attribute.name} belongs to {attribute.category.name}, which is "
                        f"not {self.product.category.name} nor above it."
                    )
                }
            )
        if is_empty(self.value):
            raise ValidationError({"value": "Fill this in, or delete the row."})
        try:
            self.value = attribute.normalize(self.value)
        except ValidationError as error:
            raise ValidationError({"value": "; ".join(error.messages)}) from error


class OfferCondition(models.TextChoices):
    NEW = "new", "New"
    REFURBISHED = "refurbished", "Refurbished"
    USED = "used", "Used"


class ProductOfferQuerySet(models.QuerySet["ProductOffer"]):
    def live(self) -> "ProductOfferQuerySet":
        """Offers a shopper could actually buy from: shown, by a shown seller."""
        return self.filter(is_active=True, seller__is_active=True)

    def sellable(self) -> "ProductOfferQuerySet":
        """The same, and with something on the shelf or a backorder allowed."""
        return self.live().filter(Q(stock__gt=0) | Q(product__allow_backorder=True))


class ProductOffer(ShopModel):
    """One seller's price and stock for one thing.

    The product row is already the primary seller's offer -- its ``price``, its
    ``stock``, its ``seller``. This table is everybody else, which is what keeps
    a single-vendor shop from carrying a join it never uses: no offers means the
    product page works exactly as it did before there was a marketplace.

    ``variant`` follows the same rule cart lines and stock do: a product sold in
    variants is offered per variant, because "£19, 4 in stock" is meaningless
    for a shirt that exists in four sizes.
    """

    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name="offers")
    seller = models.ForeignKey(Seller, on_delete=models.CASCADE, related_name="offers")
    variant = models.ForeignKey(
        ProductVariant,
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="offers",
        help_text="Required for a product sold in variants, and empty for one that is not.",
    )
    sku = models.CharField("SKU", max_length=64, blank=True, help_text="The seller's own code.")
    price = models.DecimalField(
        **MONEY,
        validators=[MinValueValidator(Decimal("0"))],
        help_text="What this seller charges. Campaigns apply to it the same way.",
    )
    stock = models.IntegerField(default=0)
    condition = models.CharField(
        max_length=16, choices=OfferCondition.choices, default=OfferCondition.NEW
    )
    lead_time_days = models.PositiveSmallIntegerField(
        default=0, help_text="Days before this seller dispatches it."
    )
    is_active = models.BooleanField(
        "Shown", default=True, help_text="A hidden offer stays in the admin and leaves the API."
    )

    objects = ProductOfferQuerySet.as_manager()

    class Meta:
        verbose_name = "Seller offer"
        verbose_name_plural = "Seller offers"
        ordering = ("price", "lead_time_days")
        # Twice, because NULLs do not collide in SQL: the first constraint says
        # nothing at all about a product without variants, where `variant` is
        # NULL on every row. The second is that case.
        constraints = (
            models.UniqueConstraint(
                fields=("product", "variant", "seller"),
                name="unique_offer_per_seller",
                violation_error_message="That seller already offers this.",
            ),
            models.UniqueConstraint(
                fields=("product", "seller"),
                condition=Q(variant__isnull=True),
                name="unique_offer_per_seller_without_variant",
                violation_error_message="That seller already offers this.",
            ),
        )
        indexes = (
            models.Index(fields=("product", "is_active")),
            models.Index(fields=("seller", "is_active")),
        )

    def __str__(self) -> str:
        return f"{self.seller.name}: {self.price}"

    def clean(self) -> None:
        super().clean()
        if not self.product_id:
            return
        if self.variant_id and self.variant.product_id != self.product_id:
            raise ValidationError({"variant": "That variant belongs to another product."})
        if self.product.has_variants and not self.variant_id:
            raise ValidationError(
                {"variant": "This product is sold in variants, so an offer names one."}
            )
        if not self.product.has_variants and self.variant_id:
            raise ValidationError({"variant": "This product has no variants."})

    @property
    def in_stock(self) -> bool:
        return not self.product.track_inventory or self.product.allow_backorder or self.stock > 0


class Collection(ActivatableModel):
    """A list somebody chose by hand: "Ready for winter", "Staff picks".

    The computed listings -- best selling, most liked, newest -- answer "what is
    true of the catalogue". This answers "what do we want to say this week", and
    no ordering derived from the data can produce it.
    """

    name = models.CharField(max_length=150)
    slug = models.SlugField(max_length=150, unique=True)
    description = models.TextField(blank=True)
    image = models.URLField(blank=True)
    order = models.PositiveIntegerField(default=0)
    products = models.ManyToManyField(
        Product, through="CollectionItem", related_name="collections", blank=True
    )

    class Meta:
        verbose_name = "Collection"
        verbose_name_plural = "Collections"
        ordering = ("order", "name")

    def __str__(self) -> str:
        return self.name


class CollectionItem(ShopModel):
    """One product's place in one collection. A through model, because order matters."""

    collection = models.ForeignKey(Collection, on_delete=models.CASCADE, related_name="items")
    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name="collection_items")
    order = models.PositiveIntegerField(default=0)

    class Meta:
        verbose_name = "Collection item"
        verbose_name_plural = "Collection items"
        ordering = ("order",)
        constraints = (
            models.UniqueConstraint(
                fields=("collection", "product"),
                name="unique_collection_product",
                violation_error_message="That product is already in this collection.",
            ),
        )

    def __str__(self) -> str:
        return f"{self.collection.name} - {self.product.name}"


class DiscountKind(models.TextChoices):
    PERCENT = "percent", "Percent off"
    AMOUNT = "amount", "Amount off"


class DiscountQuerySet(models.QuerySet["Discount"]):
    def running(self, at: datetime | None = None) -> "DiscountQuerySet":
        """The campaigns that are on right now.

        A window with open ends: no start means "since always", no end means
        "until somebody turns it off". Both are things shops actually want, and
        requiring dates for them would mean typing 2099 into a form.
        """
        moment = at or timezone.now()
        return self.filter(is_active=True).filter(
            Q(starts_at__isnull=True) | Q(starts_at__lte=moment),
            Q(ends_at__isnull=True) | Q(ends_at__gte=moment),
        )


class Discount(ShopModel):
    """A campaign: this much off these things, between these dates.

    There is no coupon code here, and the omission is deliberate: a code is a
    thing a shopper types at checkout, and it lives on :class:`Coupon` for that
    reason. The difference is what each one can honestly answer. A campaign
    applies by itself, so a product page can print the price a shopper will pay;
    a coupon depends on what somebody types into a basket, so it cannot be shown
    on a product page at all.
    """

    name = models.CharField(max_length=150, help_text="Shown to shoppers: Summer sale.")
    description = models.TextField(blank=True)
    kind = models.CharField(
        max_length=16, choices=DiscountKind.choices, default=DiscountKind.PERCENT
    )
    value = models.DecimalField(
        **MONEY,
        validators=[MinValueValidator(Decimal("0"))],
        help_text="Percent off, or an amount off, depending on the kind above.",
    )
    max_amount = models.DecimalField(
        **MONEY,
        null=True,
        blank=True,
        validators=[MinValueValidator(Decimal("0"))],
        help_text="A cap, for a percentage: 20% off, at most 50.",
    )
    starts_at = models.DateTimeField(
        null=True, blank=True, help_text="Leave empty to start immediately."
    )
    ends_at = models.DateTimeField(
        null=True, blank=True, help_text="Leave empty to run until it is turned off."
    )
    is_active = models.BooleanField(default=True)
    priority = models.IntegerField(
        default=0,
        help_text=("Breaks a tie when two campaigns save a shopper the same amount. Higher wins."),
    )
    applies_to_all = models.BooleanField(
        "Everything in the shop",
        default=False,
        help_text="Ignores the two lists below.",
    )
    products = models.ManyToManyField(Product, blank=True, related_name="discounts")
    categories = models.ManyToManyField(
        Category,
        blank=True,
        related_name="discounts",
        help_text="Includes every category underneath the ones chosen.",
    )

    objects = DiscountQuerySet.as_manager()

    class Meta:
        verbose_name = "Discount"
        verbose_name_plural = "Discounts"
        ordering = ("-priority", "-starts_at")
        indexes = (models.Index(fields=("is_active", "starts_at", "ends_at")),)

    def __str__(self) -> str:
        return self.name

    def clean(self) -> None:
        super().clean()
        if self.kind == DiscountKind.PERCENT and not (0 < self.value <= 100):
            raise ValidationError({"value": "A percentage off is between 0 and 100."})
        if self.kind == DiscountKind.AMOUNT:
            if self.value <= 0:
                raise ValidationError({"value": "An amount off has to be more than nothing."})
            if self.max_amount is not None:
                raise ValidationError(
                    {"max_amount": "A cap only means something for a percentage."}
                )
        if self.starts_at and self.ends_at and self.ends_at <= self.starts_at:
            raise ValidationError({"ends_at": "The end of the sale is before its start."})

    @property
    def is_running(self) -> bool:
        now = timezone.now()
        if not self.is_active:
            return False
        if self.starts_at and self.starts_at > now:
            return False
        return not (self.ends_at and self.ends_at < now)

    def category_branch_ids(self) -> set[uuid.UUID]:
        """Every category this campaign reaches, including the ones underneath."""
        reached: set[uuid.UUID] = set()
        for category in self.categories.all():
            reached.update(category.branch_ids())
        return reached

    def covers(self, product: Product, *, branch_ids: set[uuid.UUID] | None = None) -> bool:
        """Whether this campaign applies to one product."""
        if self.applies_to_all:
            return True
        reached = self.category_branch_ids() if branch_ids is None else branch_ids
        if product.category_id in reached:
            return True
        return any(chosen.pk == product.pk for chosen in self.products.all())


class Cart(ShopModel):
    """One account's basket. There is exactly one, and it is never deleted.

    A cart per account rather than a cart per session: a session cart needs a
    merge policy for the moment somebody signs in, and every merge policy loses
    an argument with somebody. A project that wants anonymous baskets is better
    served keeping them in the client until there is an account to attach them
    to.
    """

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="shop_cart"
    )

    class Meta:
        verbose_name = "Cart"
        verbose_name_plural = "Carts"
        ordering = ("-updated_at",)

    def __str__(self) -> str:
        return f"{self.user}'s cart"

    @classmethod
    def for_user(cls, user: Any) -> Self:
        """This account's cart, made on the spot the first time it is asked for."""
        cart, _ = cls.objects.get_or_create(user=user)
        return cart


class CartItem(ShopModel):
    """One line of one basket: this many of this product, in this variant, from
    this seller."""

    cart = models.ForeignKey(Cart, on_delete=models.CASCADE, related_name="items")
    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name="cart_items")
    variant = models.ForeignKey(
        ProductVariant,
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="cart_items",
    )
    offer = models.ForeignKey(
        "ProductOffer",
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="cart_items",
        help_text="Which seller's offer this line is from. Empty means the product's own.",
    )
    quantity = models.PositiveIntegerField(default=1, validators=[MinValueValidator(1)])

    class Meta:
        verbose_name = "Cart item"
        verbose_name_plural = "Cart items"
        ordering = ("created_at",)
        # One line per (product, variant, seller), spelled out four times
        # because NULLs do not collide in SQL: a single constraint over columns
        # that may be NULL says nothing at all about the rows where they are.
        # Two nullable columns is four cases, and each of them is a real basket.
        constraints = (
            models.UniqueConstraint(
                fields=("cart", "product", "variant", "offer"),
                name="unique_cart_line",
                violation_error_message="That is already in this cart.",
            ),
            models.UniqueConstraint(
                fields=("cart", "product", "variant"),
                condition=Q(offer__isnull=True),
                name="unique_cart_line_without_offer",
                violation_error_message="That is already in this cart.",
            ),
            models.UniqueConstraint(
                fields=("cart", "product", "offer"),
                condition=Q(variant__isnull=True),
                name="unique_cart_line_without_variant",
                violation_error_message="That is already in this cart.",
            ),
            models.UniqueConstraint(
                fields=("cart", "product"),
                condition=Q(variant__isnull=True, offer__isnull=True),
                name="unique_cart_line_plain",
                violation_error_message="That is already in this cart.",
            ),
        )
        indexes = (models.Index(fields=("cart", "created_at")),)

    def __str__(self) -> str:
        return f"{self.quantity} x {self.product.name}"

    def clean(self) -> None:
        super().clean()
        ceiling = options.max_item_quantity()
        if self.quantity > ceiling:
            raise ValidationError({"quantity": f"At most {ceiling} of one thing per basket."})
        if not self.product_id:
            return
        if self.product.has_variants and self.variant_id is None:
            raise ValidationError({"variant": "This product is sold in variants. Pick one."})
        if not self.product.has_variants and self.variant_id is not None:
            raise ValidationError({"variant": "This product has no variants."})
        if self.variant_id and self.variant.product_id != self.product_id:
            raise ValidationError({"variant": "That variant belongs to a different product."})
        if self.offer_id and self.offer.product_id != self.product_id:
            raise ValidationError({"offer": "That offer belongs to a different product."})
        if self.offer_id and self.offer.variant_id != self.variant_id:
            raise ValidationError({"offer": "That offer is for a different variant."})


class ReviewStatus(models.TextChoices):
    PENDING = "pending", "Waiting for a moderator"
    APPROVED = "approved", "Published"
    REJECTED = "rejected", "Rejected"


class ReviewQuerySet(models.QuerySet["Review"]):
    def published(self) -> "ReviewQuerySet":
        return self.filter(status=ReviewStatus.APPROVED)


class Review(ShopModel):
    """What one account thought of one product. One review each, editable.

    One per account is a constraint rather than a convention: without it, the
    rating average is a measure of how many times somebody was willing to click,
    and the first person to notice runs a script. Somebody who changes their
    mind edits the review they already wrote.
    """

    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name="reviews")
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="shop_reviews"
    )
    rating = models.PositiveSmallIntegerField(
        validators=[MinValueValidator(MIN_RATING), MaxValueValidator(MAX_RATING)],
        help_text=f"{MIN_RATING} to {MAX_RATING}.",
    )
    title = models.CharField(max_length=200, blank=True)
    body = models.TextField(blank=True)
    status = models.CharField(
        max_length=16, choices=ReviewStatus.choices, default=ReviewStatus.PENDING
    )
    moderator_note = models.CharField(
        max_length=300, blank=True, help_text="Why this was rejected. Never shown to shoppers."
    )

    objects = ReviewQuerySet.as_manager()

    class Meta:
        verbose_name = "Review"
        verbose_name_plural = "Reviews"
        ordering = ("-created_at",)
        constraints = (
            models.UniqueConstraint(
                fields=("product", "user"),
                name="one_review_per_account_per_product",
                violation_error_message="You have already reviewed this product.",
            ),
        )
        indexes = (
            models.Index(fields=("product", "status", "-created_at")),
            models.Index(fields=("user", "-created_at")),
        )

    def __str__(self) -> str:
        return f"{self.user} on {self.product.name}: {self.rating}"

    def save(self, *args: Any, **kwargs: Any) -> None:
        super().save(*args, **kwargs)
        refresh_review_stats(self.product_id)

    def delete(self, *args: Any, **kwargs: Any) -> Any:
        product_id = self.product_id
        deleted = super().delete(*args, **kwargs)
        refresh_review_stats(product_id)
        return deleted


class ProductLike(ShopModel):
    """One account having marked one product. The row's existence is the like.

    Not a counter on the product and not a boolean anywhere: the two questions
    a storefront asks are "how many" and "did I", and only a row per account can
    answer the second.
    """

    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name="likes")
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="shop_likes"
    )

    class Meta:
        verbose_name = "Like"
        verbose_name_plural = "Likes"
        ordering = ("-created_at",)
        constraints = (
            models.UniqueConstraint(
                fields=("product", "user"),
                name="one_like_per_account_per_product",
                violation_error_message="You have already liked this product.",
            ),
        )
        indexes = (models.Index(fields=("user", "-created_at")),)

    def __str__(self) -> str:
        return f"{self.user} likes {self.product.name}"

    def save(self, *args: Any, **kwargs: Any) -> None:
        super().save(*args, **kwargs)
        refresh_like_count(self.product_id)

    def delete(self, *args: Any, **kwargs: Any) -> Any:
        product_id = self.product_id
        deleted = super().delete(*args, **kwargs)
        refresh_like_count(product_id)
        return deleted


#: Where an order is. `PENDING` is the only state a shopper can leave on their
#: own -- by paying, or by cancelling; the rest are the shop's to set.
class OrderStatus(models.TextChoices):
    PENDING = "pending", "Awaiting payment"
    PAID = "paid", "Paid"
    PROCESSING = "processing", "Processing"
    SHIPPED = "shipped", "Shipped"
    COMPLETED = "completed", "Completed"
    CANCELLED = "cancelled", "Cancelled"
    REFUNDED = "refunded", "Refunded"


#: Which status an order may move to from the one it is in. A shop's order cycle
#: is a handful of one-way steps, and the two that matter -- cancelling and
#: refunding -- put stock back, so "anything to anything" is not a policy but the
#: absence of one. Written here rather than in the admin because the service
#: enforces it and all three transports go through the service.
#:
#: Cancelling stops at ``PENDING`` on purpose. Once money has been collected the
#: way out is a refund, which gives it back; letting a paid order be cancelled
#: would put the stock on the shelf and leave the payment sitting there saying
#: the shop was paid for something it no longer owes. Nothing walks backwards
#: either: an order that was refunded is not returned to paid, because the money
#: went out and this row is the record of that.
#:
#: Every member is spelled ``str(...)``: a ``TextChoices`` member is a
#: ``(value, label)`` pair to a type checker, and only its ``__str__`` is the
#: value a status column actually holds.
ORDER_TRANSITIONS: dict[str, tuple[str, ...]] = {
    str(OrderStatus.PENDING): (str(OrderStatus.PAID), str(OrderStatus.CANCELLED)),
    str(OrderStatus.PAID): (str(OrderStatus.PROCESSING), str(OrderStatus.REFUNDED)),
    str(OrderStatus.PROCESSING): (str(OrderStatus.SHIPPED), str(OrderStatus.REFUNDED)),
    str(OrderStatus.SHIPPED): (str(OrderStatus.COMPLETED), str(OrderStatus.REFUNDED)),
    str(OrderStatus.COMPLETED): (str(OrderStatus.REFUNDED),),
    str(OrderStatus.CANCELLED): (),
    str(OrderStatus.REFUNDED): (),
}

#: The states in which nothing has been dispatched, so the stock an order took
#: off the shelf is still the shop's to put back.
RESTOCKING_STATUSES = frozenset({str(OrderStatus.CANCELLED), str(OrderStatus.REFUNDED)})


class PaymentStatus(models.TextChoices):
    PENDING = "pending", "Pending"
    SUCCEEDED = "succeeded", "Succeeded"
    FAILED = "failed", "Failed"
    REFUNDED = "refunded", "Refunded"


class AddressQuerySet(models.QuerySet["Address"]):
    def default_first(self) -> "AddressQuerySet":
        """The order an address book is read in: the chosen one, then the newest."""
        return self.order_by("-is_default", "-updated_at")


class Address(ShopModel):
    """Somewhere to send it, kept so a shopper types it once.

    An order copies these fields rather than pointing at the row -- see
    :meth:`snapshot` -- so editing an address here never rewrites an order that
    was already placed.
    """

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="shop_addresses"
    )
    label = models.CharField(max_length=80, default="Home")
    full_name = models.CharField(max_length=160)
    phone = models.CharField(max_length=40)
    country = models.CharField(max_length=2)
    province = models.CharField(max_length=100, blank=True)
    city = models.CharField(max_length=100)
    postal_code = models.CharField(max_length=32)
    line1 = models.CharField(max_length=200)
    line2 = models.CharField(max_length=200, blank=True)
    is_default = models.BooleanField(
        default=False, help_text="The one a checkout offers first. There is at most one."
    )

    objects = AddressQuerySet.as_manager()

    class Meta:
        verbose_name = "Delivery address"
        verbose_name_plural = "Delivery addresses"
        ordering = ("-is_default", "-updated_at")

    def __str__(self) -> str:
        return f"{self.label}: {self.full_name}"

    def save(self, *args: Any, **kwargs: Any) -> None:
        """Keep "the default" meaning exactly one address.

        Enforced here rather than as a constraint because the rule is not that
        the column is unique -- an account with no addresses at all has no
        default, and that is fine. It is that choosing one unchooses the last,
        which is a write to a second row and therefore not something a database
        constraint can do. The first address an account saves becomes the
        default on its own, because a shopper who has typed one address in has
        already told the shop which one they mean.
        """
        with transaction.atomic():
            if not self.is_default and not Address.objects.filter(user_id=self.user_id).exists():
                self.is_default = True
            super().save(*args, **kwargs)
            if self.is_default:
                Address.objects.filter(user_id=self.user_id).exclude(pk=self.pk).update(
                    is_default=False
                )

    def clean(self) -> None:
        super().clean()
        self.country = (self.country or "").upper()
        if len(self.country) != 2 or not self.country.isalpha():
            raise ValidationError({"country": "A two-letter ISO country code, for example GB."})

    def snapshot(self) -> dict[str, str]:
        """This address as flat text, for an order to keep a copy of.

        Deliberately not a foreign key on the order: a delivery address is a
        fact about a parcel that has already gone out, and it has to stay
        readable after the shopper has edited or deleted the row it came from.
        """
        return {
            field: str(getattr(self, field))
            for field in (
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


class ShippingMethod(ShopModel):
    """How it gets there, what that costs, and how long it takes.

    ``free_from`` is here rather than expressed as a discount because free
    delivery over a threshold is a property of the delivery option, not a
    campaign on the catalogue -- and a shopper comparing two options needs to
    see it beside the price of each.
    """

    name = models.CharField(max_length=120, unique=True)
    description = models.TextField(blank=True)
    price = models.DecimalField(
        **MONEY, default=Decimal("0"), validators=[MinValueValidator(Decimal("0"))]
    )
    free_from = models.DecimalField(
        **MONEY,
        null=True,
        blank=True,
        validators=[MinValueValidator(Decimal("0"))],
        help_text="Delivery is free on a basket at least this big. Empty means never.",
    )
    min_days = models.PositiveSmallIntegerField(default=1)
    max_days = models.PositiveSmallIntegerField(default=3)
    is_active = models.BooleanField(default=True)
    order = models.PositiveIntegerField(default=0)

    class Meta:
        verbose_name = "Shipping method"
        verbose_name_plural = "Shipping methods"
        ordering = ("order", "name")

    def __str__(self) -> str:
        return self.name

    def clean(self) -> None:
        super().clean()
        if self.max_days < self.min_days:
            raise ValidationError(
                {"max_days": "The slowest this arrives cannot be sooner than the fastest."}
            )

    def cost_for(self, subtotal: Decimal) -> Decimal:
        """What this option costs for a basket of that size."""
        return (
            Decimal("0")
            if self.free_from is not None and subtotal >= self.free_from
            else self.price
        )


class Coupon(ShopModel):
    """A code a shopper types, worth a percentage or an amount off.

    The other half of :class:`Discount`, and separate from it for a reason: a
    campaign applies by itself and can therefore be shown on a product page,
    while a coupon depends on what somebody types into a basket and cannot. The
    limits here -- a window, a minimum, a number of uses -- are the ones a shop
    reaches for when a code escapes onto a deals site.
    """

    code = models.CharField(max_length=40, unique=True)
    percent = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        null=True,
        blank=True,
        validators=[MinValueValidator(Decimal("0")), MaxValueValidator(Decimal("100"))],
        help_text="Percent off the basket. Fill this in or the amount, not both.",
    )
    amount = models.DecimalField(
        **MONEY,
        null=True,
        blank=True,
        validators=[MinValueValidator(Decimal("0"))],
        help_text="A flat amount off. Fill this in or the percentage, not both.",
    )
    minimum_subtotal = models.DecimalField(
        **MONEY, default=Decimal("0"), validators=[MinValueValidator(Decimal("0"))]
    )
    starts_at = models.DateTimeField(null=True, blank=True)
    ends_at = models.DateTimeField(null=True, blank=True)
    usage_limit = models.PositiveIntegerField(null=True, blank=True)
    used_count = models.PositiveIntegerField(default=0, editable=False)
    is_active = models.BooleanField(default=True)

    class Meta:
        verbose_name = "Coupon"
        verbose_name_plural = "Coupons"
        ordering = ("code",)

    def __str__(self) -> str:
        return self.code

    def clean(self) -> None:
        """One of the two, worth something, over a window that runs forwards.

        A coupon with neither a percentage nor an amount is worth nothing and
        silently takes nothing off, which looks to a shopper exactly like a code
        that was rejected. A coupon with both is a question nobody has answered
        -- :meth:`saving` would pick the percentage, and whoever typed the
        amount in would never find out. Both are refused here rather than at
        checkout, where the person who could fix it is not the person reading
        the error.
        """
        super().clean()
        self.code = self.code.strip().upper()
        if (self.percent is None) == (self.amount is None):
            raise ValidationError(
                {"percent": "A coupon is worth either a percentage or an amount. Fill in one."}
            )
        if self.percent is not None and not (0 < self.percent <= 100):
            raise ValidationError({"percent": "A percentage off is between 0 and 100."})
        if self.amount is not None and self.amount <= 0:
            raise ValidationError({"amount": "An amount off has to be more than nothing."})
        if self.starts_at and self.ends_at and self.ends_at <= self.starts_at:
            raise ValidationError({"ends_at": "The code stops working before it starts."})

    @property
    def is_running(self) -> bool:
        """Whether the code is live right now, before any basket is considered."""
        now = timezone.now()
        return bool(
            self.is_active
            and (self.starts_at is None or self.starts_at <= now)
            and (self.ends_at is None or self.ends_at > now)
            and (self.usage_limit is None or self.used_count < self.usage_limit)
        )

    def refusal_for(self, subtotal: Decimal) -> str:
        """Why this code may not be used on a basket that size -- or "" if it may.

        A sentence rather than a boolean, because "this coupon is not available"
        is the least useful thing a checkout page can say: a shopper eight
        pounds short of the minimum will add something, and one holding an
        expired code will stop trying.
        """
        now = timezone.now()
        if not self.is_active:
            return "That code is no longer in use."
        if self.starts_at is not None and self.starts_at > now:
            return "That code is not in use yet."
        if self.ends_at is not None and self.ends_at <= now:
            return "That code has expired."
        if self.usage_limit is not None and self.used_count >= self.usage_limit:
            return "That code has been used as many times as it can be."
        if subtotal < self.minimum_subtotal:
            return f"That code needs a basket of at least {self.minimum_subtotal}."
        return ""

    def valid_for(self, subtotal: Decimal) -> bool:
        """Whether this code may be used on a basket of that size, right now.

        The same question :meth:`refusal_for` answers, asked by the code that
        only needs yes or no. Written in terms of it rather than beside it, so
        the two cannot come to disagree about what an expired coupon is.
        """
        return not self.refusal_for(subtotal)

    def saving(self, subtotal: Decimal) -> Decimal:
        """What it takes off, never more than the basket holds."""
        raw = (
            subtotal * self.percent / Decimal("100")
            if self.percent is not None
            else (self.amount or Decimal("0"))
        )
        return min(subtotal, raw.quantize(Decimal("0.01")))


class Order(ShopModel):
    """What somebody agreed to buy, at the prices they agreed to.

    Every money column here is written once and never recomputed, and the
    address is a copy rather than a reference, because this row has to answer
    "what did we charge, and where did it go" long after the catalogue has moved
    on. ``user`` is protected rather than cascading: deleting an account must not
    silently take its order history with it.
    """

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="shop_orders"
    )
    number = models.CharField(max_length=24, unique=True, editable=False)
    status = models.CharField(
        max_length=16, choices=OrderStatus.choices, default=OrderStatus.PENDING
    )
    currency = models.CharField(max_length=3)
    shipping_address = models.JSONField(default=dict)
    shipping_method = models.ForeignKey(
        ShippingMethod, null=True, on_delete=models.SET_NULL, related_name="shop_orders"
    )
    coupon = models.ForeignKey(Coupon, null=True, blank=True, on_delete=models.SET_NULL)
    subtotal = models.DecimalField(**MONEY)
    coupon_discount = models.DecimalField(**MONEY, default=Decimal("0"))
    shipping_total = models.DecimalField(**MONEY, default=Decimal("0"))
    tax_total = models.DecimalField(**MONEY, default=Decimal("0"))
    total = models.DecimalField(**MONEY)
    note = models.TextField(blank=True)
    # How it is getting there. Written when somebody in the admin dispatches it,
    # and never by the shopper -- which is why they are plain columns on the
    # order rather than anything the checkout fills in.
    carrier = models.CharField(
        max_length=80, blank=True, help_text="Who is carrying it: Royal Mail, DHL."
    )
    tracking_number = models.CharField(max_length=120, blank=True)
    tracking_url = models.URLField(
        max_length=500, blank=True, help_text="Where the shopper can follow it."
    )
    shipped_at = models.DateTimeField(null=True, blank=True, editable=False)

    class Meta:
        verbose_name = "Order"
        verbose_name_plural = "Orders"
        ordering = ("-created_at",)

    def __str__(self) -> str:
        return self.number

    def save(self, *args: Any, **kwargs: Any) -> None:
        """Give a new order the number a shopper and a support agent will quote.

        Dated and then random, rather than sequential: a number a customer reads
        out over the phone should not tell them how many orders the shop took
        this month.
        """
        if not self.number:
            self.number = f"S{timezone.now():%Y%m%d}{uuid.uuid4().hex[:8].upper()}"
        super().save(*args, **kwargs)

    @property
    def next_statuses(self) -> tuple[str, ...]:
        """Where this order may go from here. The admin draws its buttons from this."""
        return ORDER_TRANSITIONS.get(str(self.status), ())

    def may_become(self, status: str) -> bool:
        return status in self.next_statuses

    @property
    def is_open(self) -> bool:
        """Whether the shop still owes this shopper something."""
        return str(self.status) not in RESTOCKING_STATUSES and str(self.status) != (
            OrderStatus.COMPLETED
        )

    @property
    def paid_amount(self) -> Decimal:
        """What has actually been collected against this order."""
        settled = self.payments.filter(status=PaymentStatus.SUCCEEDED)
        return sum((payment.amount for payment in settled), Decimal("0.00"))


class OrderItem(ShopModel):
    """One line of an order, carrying its own copy of what was sold.

    The name, the SKU and the price are duplicated onto this row on purpose: a
    product that is renamed, repriced or archived must not change what an old
    invoice says, and both foreign keys are nullable so that removing a product
    leaves the order readable.
    """

    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name="items")
    product = models.ForeignKey(Product, null=True, on_delete=models.SET_NULL)
    variant = models.ForeignKey(ProductVariant, null=True, blank=True, on_delete=models.SET_NULL)
    offer = models.ForeignKey(
        ProductOffer, null=True, blank=True, on_delete=models.SET_NULL, related_name="order_items"
    )
    seller = models.ForeignKey(
        Seller, null=True, blank=True, on_delete=models.SET_NULL, related_name="order_items"
    )
    product_name = models.CharField(max_length=250)
    seller_name = models.CharField(
        max_length=150,
        blank=True,
        help_text="Who sold it, copied down. Empty means the shop itself.",
    )
    sku = models.CharField(max_length=64)
    quantity = models.PositiveIntegerField()
    unit_price = models.DecimalField(**MONEY)
    tax_rate = models.DecimalField(max_digits=5, decimal_places=2, default=Decimal("0"))
    line_total = models.DecimalField(**MONEY)

    class Meta:
        verbose_name = "Order line"
        verbose_name_plural = "Order lines"
        ordering = ("created_at",)

    def __str__(self) -> str:
        return f"{self.quantity} × {self.product_name}"


class OrderEvent(ShopModel):
    """One thing that happened to one order, in the order it happened.

    A row per step rather than a set of ``shipped_at``/``cancelled_at`` columns
    on the order, for the reason :class:`Payment` is a row per attempt: the
    columns answer "when", and the question a shop is actually asked is "what
    happened, in what order, and who did it". Refunding an order that was
    shipped after being cancelled and reinstated is four rows here and an
    unanswerable mess of nullable timestamps there.

    ``actor`` is nullable and set to null rather than cascading, because the
    trail has to outlive the account of whoever left it, and because a step a
    webhook took was not taken by anybody.
    """

    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name="events")
    status = models.CharField(
        max_length=16, choices=OrderStatus.choices, help_text="What the order became."
    )
    note = models.CharField(max_length=300, blank=True)
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="shop_order_events",
        help_text="Who moved it. Empty for a step a gateway or a job took.",
    )

    class Meta:
        verbose_name = "Order event"
        verbose_name_plural = "Order events"
        ordering = ("created_at",)
        indexes = (models.Index(fields=("order", "created_at")),)

    def __str__(self) -> str:
        return f"{self.order.number}: {self.get_status_display()}"


class Payment(ShopModel):
    """One attempt to collect what an order is worth.

    A row per attempt rather than a status on the order, because a failed card
    followed by a successful one is two things that happened and a shop is asked
    about both. ``idempotency_key`` is what makes a provider safe to hand this
    to: a gateway that retries its webhook settles the same payment rather than
    taking the money twice.
    """

    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name="payments")
    provider = models.CharField(max_length=48, default="manual")
    status = models.CharField(
        max_length=16, choices=PaymentStatus.choices, default=PaymentStatus.PENDING
    )
    amount = models.DecimalField(**MONEY)
    currency = models.CharField(max_length=3)
    idempotency_key = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    provider_reference = models.CharField(max_length=160, blank=True)
    paid_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = "Payment"
        verbose_name_plural = "Payments"
        ordering = ("-created_at",)

    def __str__(self) -> str:
        return f"{self.provider} {self.amount} for {self.order}"


class InventoryReservation(ShopModel):
    """Stock this order has taken off the shelf, and whether it was put back.

    Written when the order is placed rather than when it is paid, so the last
    one in stock cannot be sold twice while two shoppers are both on a checkout
    page. ``released_at`` rather than deleting the row: a cancellation has to be
    able to tell "already released" from "never reserved", and putting the same
    stock back twice is the bug this column exists to prevent.
    """

    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name="reservations")
    product = models.ForeignKey(Product, on_delete=models.PROTECT)
    variant = models.ForeignKey(ProductVariant, null=True, blank=True, on_delete=models.PROTECT)
    offer = models.ForeignKey(
        ProductOffer,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="reservations",
        help_text="Whose shelf it came off. Empty means the product's own.",
    )
    quantity = models.PositiveIntegerField()
    released_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = "Stock reservation"
        verbose_name_plural = "Stock reservations"
        ordering = ("-created_at",)

    def __str__(self) -> str:
        return f"{self.quantity} × {self.product} for {self.order}"


class Invoice(ShopModel):
    """The document that says what was owed, issued when the order is placed.

    Separate from the order, and with a number of its own, because the two are
    different things a shop is asked about: an order is what somebody wants, an
    invoice is the demand for payment against it. They are one-to-one here and
    could be one table -- until the first credit note, at which point a shop
    needs the numbering to be its own sequence rather than the order's.

    It carries no totals. Everything printed on it is already snapshotted on the
    order and its lines, and copying the numbers a second time is a second place
    for them to disagree.
    """

    order = models.OneToOneField(Order, on_delete=models.CASCADE, related_name="invoice")
    number = models.CharField(max_length=24, unique=True, editable=False)
    issued_at = models.DateTimeField(default=timezone.now)
    due_at = models.DateTimeField(
        null=True, blank=True, help_text="When payment is expected. Blank means on receipt."
    )
    notes = models.TextField(blank=True, help_text="Printed on the invoice.")

    class Meta:
        verbose_name = "Invoice"
        verbose_name_plural = "Invoices"
        ordering = ("-issued_at",)

    def __str__(self) -> str:
        return self.number

    def save(self, *args: Any, **kwargs: Any) -> None:
        """Number it on the way in, the way the order numbers itself."""
        if not self.number:
            self.number = f"INV{timezone.now():%Y%m%d}{uuid.uuid4().hex[:8].upper()}"
        super().save(*args, **kwargs)


def refresh_review_stats(product_id: Any) -> None:
    """Recompute one product's cached rating from its published reviews.

    ``queryset.update`` rather than assigning and saving, for two reasons: it is
    one statement, and it does not run ``full_clean`` -- which would validate an
    entire product because somebody left a review on it.
    """
    published = Review.objects.filter(product_id=product_id).published()
    stats = published.aggregate(average=Avg("rating"), total=Count("id"))
    average = stats["average"] or Decimal("0")
    Product.objects.filter(pk=product_id).update(
        rating_average=round(Decimal(str(average)), 2), rating_count=stats["total"]
    )


def refresh_like_count(product_id: Any) -> None:
    """Recompute one product's cached like count."""
    total = ProductLike.objects.filter(product_id=product_id).count()
    Product.objects.filter(pk=product_id).update(like_count=total)
