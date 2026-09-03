"""Everything the shop can be asked, in one place, with the rules in it.

Three transports publish this app and none of them decides anything. A router
knows that a missing product is a 404 and that a query parameter is spelled
``limit``; it does not know that a listing called ``bestsellers`` orders by
units sold, that a cart line is capped, or that an unmoderated review is
invisible. Those are decisions, they are the same over HTTP, GraphQL and gRPC,
and they live here.

The service divides the same way the models do. The catalogue half reads and
never writes -- there is no ``create_product``, on purpose, and adding one would
be adding an authorisation model. The shopper's half writes, and every method in
it takes the caller as its first argument and starts its queryset from them, so
there is no argument anywhere that can reach another account's basket.

Two exception types, because a transport has exactly two questions to ask about
a failure: is this "no such thing" (404, or gRPC ``NOT_FOUND``) or "you cannot
do that" (400)? Anything finer is the message's job.

These methods are synchronous. They read several related tables, write inside
transactions and recompute aggregates, which is ordinary Django work; the
transports that need to be asynchronous cross over with ``sync_to_async`` once,
at their own edge, rather than this file being written twice.
"""

import json
from decimal import Decimal
from typing import Any
from uuid import UUID

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Count, F, Q, QuerySet
from django.utils import timezone

from apps.shop import options
from apps.shop.attributes import AttributeType, normalize_value
from apps.shop.models import (
    Brand,
    Cart,
    CartItem,
    Category,
    CategoryAttribute,
    Collection,
    Coupon,
    Invoice,
    Order,
    OrderItem,
    OrderStatus,
    Payment,
    PaymentStatus,
    Product,
    ProductLike,
    ProductOffer,
    ProductStatus,
    ProductVariant,
    Review,
    ReviewStatus,
    Seller,
    ShippingMethod,
)
from apps.shop.payloads import (
    brand_payload,
    cart_line_payload,
    cart_payload,
    category_payload,
    category_tree,
    collection_payload,
    detailed_products,
    invoice_payload,
    listed_products,
    offer_payload,
    order_payload,
    product_payload,
    product_summary,
    review_payload,
    seller_payload,
)
from apps.shop.pricing import (
    buy_box,
    discounted_ids,
    price_of,
    reach,
    running_discounts,
    sellable_offers,
    to_cents,
)


class ShopNotFound(LookupError):
    """No such product, category, collection or basket line -- or none this caller may see.

    Deliberately not distinguished from "exists but is a draft": saying which
    would turn the read API into a way to enumerate a shop's unreleased range,
    which is a thing competitors would very much like to have.
    """


class ShopRefused(ValueError):
    """The request made sense and the shop is not going to do it."""


#: How a client may order a product list. Ordering by price is by the *shop's*
#: price and says so: sorting by the discounted price would mean computing it
#: for every product in the catalogue before the database could sort them, which
#: is a table scan per request in exchange for a nicer-looking list.
SORTS: dict[str, tuple[str, ...]] = {
    "relevance": ("order", "-created_at"),
    "newest": ("-created_at",),
    "oldest": ("created_at",),
    "price_low": ("price", "-created_at"),
    "price_high": ("-price", "-created_at"),
    "rating": ("-rating_average", "-rating_count"),
    "popular": ("-like_count", "-rating_count"),
    "bestselling": ("-sales_count",),
    "name": ("name",),
}

#: The named lists a storefront puts on its front page. Each is a filter and an
#: ordering over the same live catalogue -- not a stored list -- so none of them
#: can go stale, and a shop gets all of them without curating anything.
#:
#: ``on_sale`` is the one that cannot be expressed as an ordering, because
#: whether a product is discounted is decided by a clock against campaign rows.
#: It is filtered in Python, over the live catalogue, and it is the reason this
#: dictionary carries a flag rather than only a queryset recipe.
LISTINGS: dict[str, dict[str, Any]] = {
    "featured": {
        "name": "Featured",
        "description": "Chosen by the shop to be shown first.",
        "filter": {"is_featured": True},
        "sort": "relevance",
    },
    "bestsellers": {
        "name": "Best sellers",
        "description": "The most units sold.",
        "filter": {},
        "sort": "bestselling",
    },
    "popular": {
        "name": "Most popular",
        "description": "The most liked.",
        "filter": {},
        "sort": "popular",
    },
    "top_rated": {
        "name": "Top rated",
        "description": "The best reviewed, counting only products somebody has reviewed.",
        "filter": {"rating_count__gt": 0},
        "sort": "rating",
    },
    "newest": {
        "name": "New arrivals",
        "description": "The most recently added.",
        "filter": {},
        "sort": "newest",
    },
    "on_sale": {
        "name": "On sale",
        "description": "Everything a discount is running on right now.",
        "filter": {},
        "sort": "relevance",
        "discounted_only": True,
    },
    "in_stock": {
        "name": "In stock",
        "description": "What can be bought today.",
        "filter": {},
        "sort": "relevance",
        "in_stock_only": True,
    },
}


class ShopService:
    """Everything a storefront and a shopper can ask of this catalogue."""

    # ------------------------------------------------------------------
    # The catalogue. Read-only, public, and the same for everybody.
    # ------------------------------------------------------------------

    def categories(self, *, with_counts: bool = True) -> list[dict[str, Any]]:
        """The whole category tree, nested, in one query.

        The counts are a second query rather than one per node, and they count a
        category's own products only -- not its descendants'. A count that
        included the tree underneath would mean "Electronics (412)" next to
        "Laptops (37)" and a shopper working out that the numbers do not add up.
        """
        rows = list(Category.objects.live().select_related("parent").order_by("order", "name"))
        counts: dict[Any, int] | None = None
        if with_counts:
            counts = dict(
                Product.objects.live()
                .values_list("category_id")
                .annotate(total=Count("id"))
                .values_list("category_id", "total")
            )
        return category_tree(rows, counts)

    def category(self, slug: str) -> dict[str, Any]:
        """One category: where it sits, what is under it, and the shape of its products."""
        category = (
            Category.objects.live()
            .select_related("parent__parent")
            .prefetch_related("children", "attributes", "parent__attributes")
            .filter(slug=slug)
            .first()
        )
        if category is None:
            raise ShopNotFound("No such category.")
        total = Product.objects.live().in_branch(category).count()
        return category_payload(category, product_count=total)

    def brands(self) -> list[dict[str, Any]]:
        return [
            brand_payload(brand)
            for brand in Brand.objects.filter(is_active=True).order_by("order", "name")
        ]

    def sellers(self) -> list[dict[str, Any]]:
        """Everybody selling in this shop, in the order the admin arranged them."""
        return [
            seller_payload(seller)
            for seller in Seller.objects.filter(is_active=True).order_by("order", "name")
        ]

    def seller(self, slug: str) -> dict[str, Any]:
        """One seller, and how much of the catalogue they carry."""
        seller = Seller.objects.filter(slug=slug, is_active=True).first()
        if seller is None:
            raise ShopNotFound("No such seller.")
        own = Product.objects.live().filter(seller=seller).count()
        offered = (
            Product.objects.live()
            .filter(offers__seller=seller, offers__is_active=True)
            .exclude(seller=seller)
            .distinct()
            .count()
        )
        return {**seller_payload(seller), "product_count": own + offered}

    def products(
        self,
        *,
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
        attributes: dict[str, Any] | None = None,
        sort: str = "relevance",
        limit: int | None = None,
        offset: int = 0,
    ) -> dict[str, Any]:
        """The one endpoint a storefront's search, filters and category pages all use.

        Written as one method rather than as a search endpoint and a category
        endpoint and a filtered endpoint, because they are the same question
        with different arguments -- and because the moment they are three
        methods, two of them stop supporting the filter that was added to the
        third.

        Returns the page and the total, because a client cannot render "showing
        1-24 of 312" without both, and asking for it separately means two
        queries against a catalogue that changed in between.
        """
        matches = listed_products().live()
        if search:
            matches = matches.search(search)
        if category:
            branch = Category.objects.live().filter(slug=category).first()
            if branch is None:
                raise ShopNotFound("No such category.")
            matches = matches.in_branch(branch)
        if brand:
            matches = matches.filter(brand__slug=brand, brand__is_active=True)
        if seller:
            # Either sold by them directly or offered by them: a shopper
            # browsing a seller's page means "everything I can buy here".
            matches = matches.filter(
                Q(seller__slug=seller, seller__is_active=True)
                | Q(
                    offers__seller__slug=seller,
                    offers__is_active=True,
                    offers__seller__is_active=True,
                )
            ).distinct()
        if tag:
            matches = matches.filter(tags__slug=tag)
        if min_price is not None:
            matches = matches.filter(price__gte=min_price)
        if max_price is not None:
            matches = matches.filter(price__lte=max_price)
        if min_rating is not None:
            matches = matches.filter(rating_average__gte=min_rating)
        if featured is not None:
            matches = matches.filter(is_featured=featured)
        if in_stock:
            matches = matches.in_stock()
        matches = self._filter_by_attributes(matches, attributes or {})
        matches = matches.order_by(*SORTS.get(sort, SORTS["relevance"]))

        if on_sale:
            return self._discounted_page(matches, limit, offset)
        return self._page(matches, limit, offset)

    def _filter_by_attributes(
        self, matches: QuerySet[Product], attributes: dict[str, Any]
    ) -> QuerySet[Product]:
        """Narrow to products answering every one of these attributes with these values.

        One ``filter`` call per attribute rather than one with several
        conditions, which is the difference between "has a red colour attribute
        and a large size attribute" and "has an attribute that is somehow both".

        The value arrives as text, because it came out of a query string, and
        the stored one is whatever its type normalises to -- ``14``, not
        ``"14"``. So each one is put through the same normaliser the write side
        uses before it is compared, and a value that will not normalise matches
        nothing: a shopper who asked for a colour this shop does not stock has
        asked a real question with an empty answer.

        A multi-choice attribute matches when the wanted value is *among* the
        ones answered, which is what ticking "USB-C" means. That is a membership
        test inside a JSON array, and the ``contains`` lookup that would express
        it is unsupported on SQLite -- which this project ships with. So it is
        done as a substring match on the encoded value instead: the needle
        carries its own quotes, so `"M"` finds ``["S", "M"]`` and not
        ``["Medium"]``.
        """
        for code, wanted in attributes.items():
            declared = (
                CategoryAttribute.objects.filter(code=code)
                .order_by("category__order", "order")
                .first()
            )
            if declared is None:
                return matches.none()
            multiple = str(declared.attribute_type) == str(AttributeType.MULTI_CHOICE)
            try:
                value = normalize_value(
                    AttributeType.CHOICE if multiple else declared.attribute_type,
                    wanted,
                    choices=declared.choices,
                )
            except ValidationError:
                return matches.none()
            answered = (
                Q(attribute_values__value__icontains=json.dumps(value))
                if multiple
                else Q(attribute_values__value=value)
            )
            matches = matches.filter(Q(attribute_values__attribute__code=code) & answered)
        return matches.distinct() if attributes else matches

    def _page(self, matches: QuerySet[Product], limit: int | None, offset: int) -> dict[str, Any]:
        """Slice a product queryset and price the rows, loading the campaigns once."""
        size, start = options.bounded_page(limit, offset)
        total = matches.count()
        rows = list(matches[start : start + size])
        discounts = running_discounts()
        branches = reach(discounts)
        return {
            "items": [
                product_summary(product, buy_box(product, None, discounts, branches)[1])
                for product in rows
            ],
            "total": total,
            "limit": size,
            "offset": start,
        }

    def _discounted_page(
        self, matches: QuerySet[Product], limit: int | None, offset: int
    ) -> dict[str, Any]:
        """The same, for "on sale", which the database cannot answer on its own.

        Whether a product is discounted depends on campaign rows and on the
        clock, and expressing that as SQL would mean teaching the database how
        this app resolves overlapping campaigns. So the live catalogue is walked
        once, in id order, and the page is taken from what is left. That is a
        full scan, and it is the honest cost of not storing a sale price that
        goes stale -- a shop whose catalogue outgrows it wants a materialised
        "on sale" flag maintained by a job, and this is the one method to change.
        """
        discounts = running_discounts()
        size, start = options.bounded_page(limit, offset)
        if not discounts:
            return {"items": [], "total": 0, "limit": size, "offset": start}
        branches = reach(discounts)
        every = list(matches)
        reached = discounted_ids(every, discounts)
        on_sale = [product for product in every if product.pk in reached]
        return {
            "items": [
                product_summary(product, buy_box(product, None, discounts, branches)[1])
                for product in on_sale[start : start + size]
            ],
            "total": len(on_sale),
            "limit": size,
            "offset": start,
        }

    def product(self, slug: str, user: Any = None) -> dict[str, Any]:
        """One product page, including this caller's own like and review.

        An archived product is still readable by slug while it is invisible to
        every listing: somebody's bookmark, somebody's review and somebody's
        order confirmation all point at it, and answering those with a 404 is
        how a shop loses the page that explains what they bought.
        """
        product = detailed_products().filter(slug=slug).exclude(status=ProductStatus.DRAFT).first()
        if product is None:
            raise ShopNotFound("No such product.")
        discounts = running_discounts()
        branches = reach(discounts)
        variant_prices = {
            variant.pk: price_of(product, variant, discounts, branches)
            for variant in product.variants.all()
        }
        liked = False
        own_review = None
        if user is not None and getattr(user, "is_authenticated", False):
            liked = ProductLike.objects.filter(product=product, user=user).exists()
            own_review = Review.objects.filter(product=product, user=user).first()
        return product_payload(
            product,
            # The headline price is what a shopper would actually be charged if
            # they pressed the button, which is the cheapest live seller -- not
            # the product row's own price when somebody undercuts it.
            buy_box(product, None, discounts, branches)[1],
            variant_prices=variant_prices,
            liked=liked,
            own_review=own_review,
            offers=[
                offer_payload(offer, price_of(product, None, discounts, branches, offer))
                for offer in sellable_offers(product)
            ],
        )

    def view(self, slug: str) -> int:
        """Record that somebody looked, and return the new count.

        A bare ``update`` with an ``F`` expression rather than read-modify-write,
        so two people opening the same page at the same time count as two.
        Separate from :meth:`product` because a page that is rendered from a
        cache still wants to count the view, and a crawler reading the JSON does
        not.
        """
        updated = Product.objects.live().filter(slug=slug).update(view_count=F("view_count") + 1)
        if not updated:
            raise ShopNotFound("No such product.")
        return int(
            Product.objects.filter(slug=slug).values_list("view_count", flat=True).first() or 0
        )

    def listings(self) -> list[dict[str, str]]:
        """What lists this shop publishes, so a client does not hard-code the keys."""
        return [
            {"key": key, "name": listing["name"], "description": listing["description"]}
            for key, listing in LISTINGS.items()
        ]

    def listing(self, key: str, *, limit: int | None = None, offset: int = 0) -> dict[str, Any]:
        """One named list: best sellers, most liked, new arrivals, on sale."""
        listing = LISTINGS.get(key)
        if listing is None:
            raise ShopNotFound(f"No such list. This shop has: {', '.join(LISTINGS)}.")
        matches = listed_products().live().filter(**listing["filter"])
        if listing.get("in_stock_only"):
            matches = matches.in_stock()
        matches = matches.order_by(*SORTS[listing["sort"]])
        page = (
            self._discounted_page(matches, limit, offset)
            if listing.get("discounted_only")
            else self._page(matches, limit, offset)
        )
        return {"key": key, "name": listing["name"], "description": listing["description"], **page}

    def collections(self) -> list[dict[str, Any]]:
        return [
            collection_payload(collection)
            for collection in Collection.objects.filter(is_active=True).order_by("order", "name")
        ]

    def collection(self, slug: str, *, limit: int | None = None) -> dict[str, Any]:
        """One hand-curated list, in the order somebody arranged it."""
        collection = Collection.objects.filter(is_active=True, slug=slug).first()
        if collection is None:
            raise ShopNotFound("No such collection.")
        size, _ = options.bounded_page(limit, 0)
        products = list(
            listed_products()
            .live()
            .filter(collection_items__collection=collection)
            .order_by("collection_items__order")[:size]
        )
        discounts = running_discounts()
        branches = reach(discounts)
        return collection_payload(
            collection,
            [
                product_summary(product, price_of(product, None, discounts, branches))
                for product in products
            ],
        )

    def related(self, slug: str, *, limit: int = 8) -> list[dict[str, Any]]:
        """Other things somebody looking at this might buy instead.

        The same category, best rated first, and never the product itself. Not a
        recommendation engine and not pretending to be one -- what it is, is the
        row every product page has and the one nobody wants to leave empty until
        there is behavioural data to fill it with.
        """
        product = Product.objects.live().filter(slug=slug).first()
        if product is None:
            raise ShopNotFound("No such product.")
        siblings = list(
            listed_products()
            .live()
            .filter(category_id=product.category_id)
            .exclude(pk=product.pk)
            .order_by("-rating_average", "-sales_count")[: max(1, limit)]
        )
        discounts = running_discounts()
        branches = reach(discounts)
        return [
            product_summary(sibling, price_of(sibling, None, discounts, branches))
            for sibling in siblings
        ]

    # ------------------------------------------------------------------
    # Reviews. Read by everybody, written by the account that owns one.
    # ------------------------------------------------------------------

    def reviews(self, slug: str, *, limit: int | None = None, offset: int = 0) -> dict[str, Any]:
        """The published reviews of one product, newest first, with the total."""
        product = self._product_row(slug)
        published = (
            Review.objects.filter(product=product).published().select_related("user", "product")
        )
        size, start = options.bounded_page(limit, offset)
        return {
            "items": [review_payload(review) for review in published[start : start + size]],
            "total": published.count(),
            "limit": size,
            "offset": start,
            "rating_average": product.rating_average,
            "rating_count": product.rating_count,
        }

    def review_product(
        self, user: Any, slug: str, *, rating: int, title: str = "", body: str = ""
    ) -> dict[str, Any]:
        """Write this account's review of a product, or replace the one it wrote.

        An update rather than a second row, because one review per account is
        the constraint that stops the rating being a measure of persistence. An
        edited review goes back through moderation where moderation is on: a
        five-star review edited into something else is exactly the trick the
        queue exists to catch.
        """
        product = self._product_row(slug)
        status = ReviewStatus.PENDING if options.review_moderation() else ReviewStatus.APPROVED
        review = Review.objects.filter(product=product, user=user).first() or Review(
            product=product, user=user
        )
        review.rating = rating
        review.title = title
        review.body = body
        review.status = status
        try:
            review.save()
        except ValidationError as invalid:
            raise ShopRefused("; ".join(invalid.messages)) from None
        review.refresh_from_db()
        return review_payload(review)

    def delete_review(self, user: Any, slug: str) -> dict[str, Any]:
        """Take back this account's own review. Somebody else's is a 404, not a 403."""
        review = Review.objects.filter(product__slug=slug, user=user).first()
        if review is None:
            raise ShopNotFound("You have not reviewed this product.")
        review.delete()
        product = self._product_row(slug)
        return {
            "product": slug,
            "rating_average": product.rating_average,
            "rating_count": product.rating_count,
        }

    def my_reviews(self, user: Any, *, limit: int | None = None, offset: int = 0) -> dict[str, Any]:
        """Everything this account has written, including what is still in the queue.

        The one place a pending review is visible, because its author is the one
        person entitled to know it exists.
        """
        mine = Review.objects.filter(user=user).select_related("product", "user")
        size, start = options.bounded_page(limit, offset)
        return {
            "items": [review_payload(review) for review in mine[start : start + size]],
            "total": mine.count(),
            "limit": size,
            "offset": start,
        }

    # ------------------------------------------------------------------
    # Likes.
    # ------------------------------------------------------------------

    def like(self, user: Any, slug: str) -> dict[str, Any]:
        """Mark a product. Idempotent: liking twice is not an error, it is a no-op."""
        product = self._product_row(slug)
        _, created = ProductLike.objects.get_or_create(product=product, user=user)
        if created:
            product.refresh_from_db(fields=["like_count"])
        return {"product": slug, "liked": True, "like_count": product.like_count}

    def unlike(self, user: Any, slug: str) -> dict[str, Any]:
        """Unmark it. Also idempotent, for the same reason."""
        product = self._product_row(slug)
        like = ProductLike.objects.filter(product=product, user=user).first()
        if like is not None:
            like.delete()
            product.refresh_from_db(fields=["like_count"])
        return {"product": slug, "liked": False, "like_count": product.like_count}

    def liked(self, user: Any, *, limit: int | None = None, offset: int = 0) -> dict[str, Any]:
        """What this account has marked, most recently first."""
        marked = listed_products().live().filter(likes__user=user).order_by("-likes__created_at")
        return self._page(marked, limit, offset)

    # ------------------------------------------------------------------
    # The cart. Every method starts from the caller.
    # ------------------------------------------------------------------

    def cart(self, user: Any) -> dict[str, Any]:
        """This account's basket, priced now.

        Created on first read rather than at sign-up: a cart row for every
        account that has never shopped is a table of nothing, and the first read
        is exactly the moment one is needed.
        """
        cart = Cart.for_user(user)
        return self._cart_payload(cart)

    def add_to_cart(
        self,
        user: Any,
        slug: str,
        *,
        variant_id: UUID | str | None = None,
        offer_id: UUID | str | None = None,
        quantity: int = 1,
    ) -> dict[str, Any]:
        """Put something in the basket, or add to the line that is already there.

        Adding what is already there increases the quantity rather than
        refusing: a shopper who clicks twice means two, and an error message
        saying "already in your cart" is a thing they have to go and fix by
        hand. "Already there" means the same seller as well as the same variant,
        so two sellers' offers of one thing are two lines.

        Naming no seller takes the one the product page was showing -- the buy
        box -- rather than always the product's own row, so the price in the
        basket is the price the shopper just read.
        """
        if quantity < 1:
            raise ShopRefused("Ask for at least one.")
        product = self._product_row(slug)
        variant = self._variant_row(product, variant_id)
        offer = self._offer_row(product, variant, offer_id)
        self._check_stock(product, variant, quantity, offer)
        with transaction.atomic():
            cart = Cart.for_user(user)
            line = CartItem.objects.filter(
                cart=cart, product=product, variant=variant, offer=offer
            ).first()
            wanted = (line.quantity if line else 0) + quantity
            ceiling = options.max_item_quantity()
            if wanted > ceiling:
                raise ShopRefused(f"At most {ceiling} of one thing per basket.")
            self._check_stock(product, variant, wanted, offer)
            if line is None:
                line = CartItem(cart=cart, product=product, variant=variant, offer=offer)
            line.quantity = wanted
            self._save_line(line)
            cart.save()
        return self._cart_payload(cart)

    def set_cart_quantity(self, user: Any, item_id: UUID | str, quantity: int) -> dict[str, Any]:
        """Set one line to a number. Zero removes it, which is what a stepper does at 1."""
        line = self._cart_line(user, item_id)
        if quantity < 1:
            return self.remove_from_cart(user, item_id)
        self._check_stock(line.product, line.variant, quantity, line.offer)
        line.quantity = quantity
        self._save_line(line)
        line.cart.save()
        return self._cart_payload(line.cart)

    def remove_from_cart(self, user: Any, item_id: UUID | str) -> dict[str, Any]:
        line = self._cart_line(user, item_id)
        cart = line.cart
        line.delete()
        cart.save()
        return self._cart_payload(cart)

    def clear_cart(self, user: Any) -> dict[str, Any]:
        cart = Cart.for_user(user)
        cart.items.all().delete()
        cart.save()
        return self._cart_payload(cart)

    def checkout(
        self,
        user: Any,
        *,
        address_id: UUID | str,
        shipping_method_id: UUID | str,
        coupon_code: str = "",
        note: str = "",
        provider: str = "manual",
    ) -> Order:
        """Atomically turn the caller's cart into an immutable order and payment intent."""
        from apps.shop.models import Address, InventoryReservation

        with transaction.atomic():
            cart = Cart.objects.select_for_update().filter(user=user).first()
            if cart is None or not cart.items.exists():
                raise ShopRefused("Your cart is empty.")
            address = Address.objects.filter(pk=address_id, user=user).first()
            shipping = ShippingMethod.objects.filter(pk=shipping_method_id, is_active=True).first()
            if address is None or shipping is None:
                raise ShopNotFound("No such delivery address or shipping method.")
            lines = list(
                cart.items.select_related("product", "variant", "offer__seller").select_for_update()
            )
            discounts, branches = running_discounts(), None
            rows: list[tuple[CartItem, Any, Any]] = []
            subtotal = Decimal("0.00")
            tax_total = Decimal("0.00")
            for line in lines:
                product = Product.objects.select_for_update().get(pk=line.product_id)
                variant = (
                    ProductVariant.objects.select_for_update().get(pk=line.variant_id)
                    if line.variant_id
                    else None
                )
                offer = (
                    ProductOffer.objects.select_for_update()
                    .select_related("seller")
                    .get(pk=line.offer_id)
                    if line.offer_id
                    else None
                )
                self._check_stock(product, variant, line.quantity, offer)
                price = price_of(product, variant, discounts, branches, offer)
                line_total = to_cents(price.amount * line.quantity)
                subtotal += line_total
                # Rounded per line and then summed, rather than summed and then
                # rounded: it is how an invoice adds up, and the totals columns
                # hold two decimal places -- a `Decimal` carrying six, which
                # this division produces, is refused outright rather than
                # rounded on the way in.
                tax_total += to_cents(line_total * product.tax_rate / Decimal("100"))
                rows.append((line, price, offer))
            coupon = (
                Coupon.objects.select_for_update().filter(code__iexact=coupon_code).first()
                if coupon_code
                else None
            )
            if coupon_code and (coupon is None or not coupon.valid_for(subtotal)):
                raise ShopRefused("This coupon is not available for this cart.")
            coupon_discount = coupon.saving(subtotal) if coupon else Decimal("0.00")
            shipping_total = shipping.cost_for(subtotal - coupon_discount)
            total = to_cents(subtotal - coupon_discount + shipping_total + tax_total)
            order = Order.objects.create(
                user=user,
                currency=options.currency(),
                shipping_address=address.snapshot(),
                shipping_method=shipping,
                coupon=coupon,
                subtotal=subtotal,
                coupon_discount=coupon_discount,
                shipping_total=shipping_total,
                tax_total=tax_total,
                total=total,
                note=note,
            )
            for line, price, offer in rows:
                seller = offer.seller if offer else line.product.seller
                OrderItem.objects.create(
                    order=order,
                    product=line.product,
                    variant=line.variant,
                    offer=offer,
                    seller=seller,
                    product_name=line.product.name,
                    seller_name=seller.name if seller else "",
                    sku=self._line_sku(line, offer),
                    quantity=line.quantity,
                    unit_price=price.amount,
                    tax_rate=line.product.tax_rate,
                    line_total=to_cents(price.amount * line.quantity),
                )
                self._move_stock(line.product, line.variant, -line.quantity, offer)
                InventoryReservation.objects.create(
                    order=order,
                    product=line.product,
                    variant=line.variant,
                    offer=offer,
                    quantity=line.quantity,
                )
            if coupon:
                Coupon.objects.filter(pk=coupon.pk).update(used_count=F("used_count") + 1)
            Payment.objects.create(
                order=order, provider=provider, amount=total, currency=options.currency()
            )
            # Issued here rather than on payment, because it is the demand for
            # payment: a shopper who has to pay by transfer needs the document
            # before the money moves, not after.
            Invoice.objects.create(order=order)
            cart.items.all().delete()
        return order

    def orders(self, user: Any, *, limit: int | None = None, offset: int = 0) -> dict[str, Any]:
        """This account's orders, newest first."""
        matches = (
            Order.objects.filter(user=user)
            .select_related("coupon", "shipping_method", "invoice")
            .prefetch_related("items__product", "payments")
            .order_by("-created_at")
        )
        size, start = options.bounded_page(limit, offset)
        return {
            "items": [order_payload(order) for order in matches[start : start + size]],
            "total": matches.count(),
            "limit": size,
            "offset": start,
        }

    def order(self, user: Any, number: str) -> dict[str, Any]:
        """One of this account's orders. Somebody else's does not exist."""
        return order_payload(self._order_row(user, number))

    def invoice(self, user: Any, number: str) -> dict[str, Any]:
        """The invoice issued against one of this account's orders."""
        order = self._order_row(user, number)
        document = Invoice.objects.filter(order=order).first()
        if document is None:
            raise ShopNotFound("No invoice has been issued for that order.")
        return invoice_payload(document)

    def settle_order(self, order: Order, *, reference: str = "", provider: str = "") -> Order:
        """Mark a pending order paid, and turn its reservations into sales.

        The one place an order becomes paid, whether that was decided by a
        provider's webhook or by somebody in the admin looking at a bank
        statement. Idempotent on purpose: a gateway that retries its callback
        must settle the same order rather than selling the stock twice.
        """
        with transaction.atomic():
            locked = Order.objects.select_for_update().get(pk=order.pk)
            if locked.status != OrderStatus.PENDING:
                return locked
            payment = (
                locked.payments.select_for_update().filter(status=PaymentStatus.PENDING).first()
            )
            if payment is None:
                raise ShopRefused("This order has no payment waiting to be settled.")
            for reservation in locked.reservations.filter(released_at__isnull=True):
                Product.objects.filter(pk=reservation.product_id).update(
                    sales_count=F("sales_count") + reservation.quantity
                )
            payment.status, payment.provider_reference, payment.paid_at = (
                PaymentStatus.SUCCEEDED,
                reference,
                timezone.now(),
            )
            if provider:
                payment.provider = provider
            payment.save(
                update_fields=["status", "provider", "provider_reference", "paid_at", "updated_at"]
            )
            locked.status = OrderStatus.PAID
            locked.save(update_fields=["status", "updated_at"])
        return locked

    def reject_payment(self, order: Order, *, reason: str = "") -> Order:
        """Record that a payment attempt did not happen, and open another.

        The order stays pending and its stock stays reserved, because a declined
        card is an attempt that failed rather than an order that is dead: a
        shopper tries a second card, and releasing the stock underneath them
        would mean the retry oversells. A fresh pending payment is opened so
        there is something for the next attempt to settle -- which is also what
        a gateway does, one intent per attempt.

        An order that is genuinely finished is cancelled instead, and that is
        what puts the stock back.
        """
        with transaction.atomic():
            locked = Order.objects.select_for_update().get(pk=order.pk)
            if locked.status != OrderStatus.PENDING:
                raise ShopRefused("Only a pending order has a payment to reject.")
            attempt = (
                locked.payments.select_for_update().filter(status=PaymentStatus.PENDING).first()
            )
            if attempt is None:
                raise ShopRefused("This order has no payment waiting to be settled.")
            attempt.status = PaymentStatus.FAILED
            attempt.provider_reference = reason or attempt.provider_reference
            attempt.save(update_fields=["status", "provider_reference", "updated_at"])
            Payment.objects.create(
                order=locked,
                provider=attempt.provider,
                amount=locked.total,
                currency=locked.currency,
            )
        return locked

    def confirm_payment(self, user: Any, number: str, *, reference: str = "") -> Order:
        """The provider-webhook seam, scoped to the account whose order it is.

        There is no gateway wired up in this starter -- the default provider is
        ``manual`` and the working path is the admin -- so this is the endpoint a
        project points its callback at once it has one, and the reason the
        settling itself lives in :meth:`settle_order` rather than here.
        """
        return self.settle_order(self._order_row(user, number), reference=reference)

    def cancel_order(self, user: Any, number: str) -> Order:
        """Cancel an unpaid order and release its stock reservation exactly once."""
        from apps.shop.models import InventoryReservation

        with transaction.atomic():
            order = Order.objects.select_for_update().filter(number=number, user=user).first()
            if order is None:
                raise ShopNotFound("No such order.")
            if order.status != OrderStatus.PENDING:
                raise ShopRefused("Only an unpaid order can be cancelled here.")
            for reservation in (
                InventoryReservation.objects.select_for_update()
                .select_related("product", "variant", "offer")
                .filter(order=order, released_at__isnull=True)
            ):
                self._move_stock(
                    reservation.product,
                    reservation.variant,
                    reservation.quantity,
                    reservation.offer,
                )
                reservation.released_at = timezone.now()
                reservation.save(update_fields=["released_at", "updated_at"])
            order.status = OrderStatus.CANCELLED
            order.save(update_fields=["status", "updated_at"])
        return order

    # ------------------------------------------------------------------
    # The small shared pieces.
    # ------------------------------------------------------------------

    def _order_row(self, user: Any, number: str) -> Order:
        """One of *this* account's orders, by the number they were given.

        The filter starts from the user, so another account's number does not
        exist as far as this method is concerned -- the same reason a cart line
        belonging to somebody else is a 404.
        """
        order = (
            Order.objects.filter(number=number, user=user)
            .select_related("coupon", "shipping_method", "invoice")
            .prefetch_related("items__product", "payments")
            .first()
        )
        if order is None:
            raise ShopNotFound("No such order.")
        return order

    def _line_sku(self, line: CartItem, offer: Any) -> str:
        """The code that identifies what was sold, most specific first.

        A seller's own code where they gave one, then the variant's, then the
        product's -- because an invoice line is quoted back to whoever has to
        find the thing on a shelf.
        """
        if offer is not None and offer.sku:
            return offer.sku
        return line.variant.sku if line.variant else line.product.sku

    def _product_row(self, slug: str) -> Product:
        product = (
            Product.objects.live().select_related("category", "brand").filter(slug=slug).first()
        )
        if product is None:
            raise ShopNotFound("No such product.")
        return product

    def _variant_row(
        self, product: Product, variant_id: UUID | str | None
    ) -> ProductVariant | None:
        """Resolve the variant a shopper picked, and insist on one where it matters."""
        if variant_id in (None, ""):
            if product.has_variants:
                raise ShopRefused("This product is sold in variants. Pick one.")
            return None
        if not product.has_variants:
            raise ShopRefused("This product has no variants.")
        try:
            variant = product.variants.filter(pk=variant_id, is_active=True).first()
        except (ValueError, ValidationError):
            variant = None
        if variant is None:
            raise ShopNotFound("No such variant of this product.")
        return variant

    def _move_stock(
        self,
        product: Product,
        variant: ProductVariant | None,
        delta: int,
        offer: Any = None,
    ) -> None:
        """Add ``delta`` to what is on the shelf, in one statement.

        ``queryset.update`` rather than assigning and saving, for the reason
        the review counters do it that way: every save in this app runs
        ``full_clean``, and an ``F`` expression is not a value a validator can
        check -- so assigning one and saving raises instead of decrementing.
        Doing it as an update is also what makes this safe against a second
        checkout running at the same time, since the database does the
        subtraction rather than a number this process read a moment ago.

        A product that is not tracked has no shelf to move. A variant and
        another seller's offer both always do: each carries its own stock
        whatever the product says.
        """
        if offer is not None:
            ProductOffer.objects.filter(pk=offer.pk).update(stock=F("stock") + delta)
        elif variant is not None:
            ProductVariant.objects.filter(pk=variant.pk).update(stock=F("stock") + delta)
        elif product.track_inventory:
            Product.objects.filter(pk=product.pk).update(stock=F("stock") + delta)

    def _offer_row(
        self, product: Product, variant: ProductVariant | None, offer_id: UUID | str | None
    ) -> Any:
        """Resolve the seller a shopper picked, or the one they were shown.

        ``None`` back means the product's own row, which is the primary seller.
        An id that is not a live offer of this product -- in this variant -- is
        a 404 rather than being quietly ignored, because ignoring it would
        charge somebody a different seller's price than the one they chose.
        """
        if offer_id in (None, ""):
            return buy_box(product, variant)[0]
        try:
            offer = (
                ProductOffer.objects.live()
                .select_related("seller")
                .filter(pk=offer_id, product=product, variant=variant)
                .first()
            )
        except (ValueError, ValidationError):
            offer = None
        if offer is None:
            raise ShopNotFound("No such offer for this product.")
        return offer

    def _check_stock(
        self,
        product: Product,
        variant: ProductVariant | None,
        quantity: int,
        offer: Any = None,
    ) -> None:
        """Refuse a basket the shop cannot fill, unless backorders are fine.

        Which shelf to count is the rule the price follows: the named seller's
        offer, then the variant, then the product.
        """
        if not product.track_inventory or product.allow_backorder:
            return
        if offer is not None:
            available = offer.stock
        elif variant is not None:
            available = variant.stock
        else:
            available = product.available_stock
        if quantity > available:
            raise ShopRefused(f"Only {available} left." if available else "That is out of stock.")

    def _save_line(self, line: CartItem) -> None:
        try:
            line.save()
        except ValidationError as invalid:
            raise ShopRefused("; ".join(invalid.messages)) from None

    def _cart_line(self, user: Any, item_id: UUID | str) -> CartItem:
        """One line of *this* account's basket. Somebody else's is a 404, not a 403.

        The filter starts from the user, so an id belonging to another basket
        does not exist as far as this method is concerned -- which is also why
        the two cases give the same answer.
        """
        try:
            line = (
                CartItem.objects.select_related("cart", "product", "variant")
                .filter(pk=item_id, cart__user=user)
                .first()
            )
        except (ValueError, ValidationError):
            line = None
        if line is None:
            raise ShopNotFound("No such item in your cart.")
        return line

    def _cart_payload(self, cart: Cart) -> dict[str, Any]:
        lines = list(
            cart.items.select_related(
                "product__brand",
                "product__category",
                "product__seller",
                "variant",
                "offer__seller",
            )
            .prefetch_related("product__images", "product__tags")
            .order_by("created_at")
        )
        discounts = running_discounts()
        branches = reach(discounts)
        return cart_payload(
            cart,
            [
                cart_line_payload(
                    line,
                    price_of(line.product, line.variant, discounts, branches, line.offer),
                )
                for line in lines
            ],
        )


shop_service = ShopService()
