"""The shop over HTTP: a catalogue anybody may read, and a basket only you can.

Every decision is :class:`ShopService`'s. What is decided here is what only HTTP
can decide: that a filter arrives as a query parameter, that a missing product
is a 404 and a refused basket is a 400, and which endpoints demand a credential.

**The catalogue is public and read-only.** There is no endpoint that creates a
product, a category or a discount, because those are written in the admin -- a
shop's catalogue is its balance sheet, and an API that writes to it needs an
authorisation model this app does not have.

**The shopper's half needs a credential and scopes itself to it.** Nothing under
the cart, the reviews or the likes takes an account id; the caller is the
account, and the service's querysets start from them.

Two endpoints sit in between. The product page and its review list are public,
but answer a little differently to somebody signed in: they carry back whether
*you* liked it and what *you* wrote. A credential is read where one is offered
and never demanded, so a signed-out shopper is served the same page without it.

Nothing here imports the project around it beyond one optional line: the
credential reader, which falls back to Django's session auth in a project that
installed no login method at all.
"""

from collections.abc import Iterator
from contextlib import contextmanager
from decimal import Decimal
from typing import Any

from django.http import HttpRequest
from ninja import Query, Router
from ninja.errors import HttpError

from apps.shop.attributes import parse_filters
from apps.shop.rest.schemas import (
    AddressIn,
    AddressOut,
    AddressPatchIn,
    AddressRemovedOut,
    BrandOut,
    CartAddIn,
    CartOut,
    CartQuantityIn,
    CategoryNodeOut,
    CategoryOut,
    CheckoutIn,
    CollectionOut,
    CouponPreviewIn,
    CouponPreviewOut,
    InvoiceOut,
    LikeOut,
    ListingOut,
    ListingSummaryOut,
    MyReviewPageOut,
    OrderOut,
    OrderPageOut,
    PaymentConfirmIn,
    ProductOut,
    ProductPageOut,
    ProductSummaryOut,
    ReviewIn,
    ReviewOut,
    ReviewPageOut,
    ReviewRemovedOut,
    SellerDetailOut,
    SellerOut,
    ShippingMethodOut,
    ViewOut,
)
from apps.shop.services import ShopNotFound, ShopRefused, shop_service

try:  # pragma: no cover - exercised by whichever branch the project installs
    from infrastructure.auth.core.sessions import api_auth, resolve_request_user
except ImportError:  # pragma: no cover - only in a project without the auth apps
    from ninja.security import django_auth as api_auth  # type: ignore[assignment]

    resolve_request_user = None  # type: ignore[assignment]

router = Router()


@contextmanager
def _answers() -> Iterator[None]:
    """Turn the service's two refusals into the two statuses they mean.

    Written once rather than at every call site: "no such thing" and "the shop
    will not do that" are the only two failures this app has, and repeating the
    mapping fifteen times is fifteen chances to answer a missing product with a
    400.
    """
    try:
        yield
    except ShopNotFound as missing:
        raise HttpError(404, str(missing)) from None
    except ShopRefused as refused:
        raise HttpError(400, str(refused)) from None


def _caller(request: HttpRequest) -> Any | None:
    """The account this request proves it is, or ``None``.

    Used by the endpoints that are public but personal. A credential is read
    where one is offered and never demanded, so the same page serves a signed-out
    shopper -- without the heart filled in.
    """
    user = getattr(request, "user", None)
    if user is not None and user.is_authenticated:
        return user
    if resolve_request_user is None:
        return None
    return resolve_request_user(request)


def _attribute_filters(pairs: list[str]) -> dict[str, str]:
    """Parse ``?attribute=colour:Red&attribute=size:M`` into what the service takes.

    The parsing itself is :func:`apps.shop.attributes.parse_filters`, so this
    door and the gRPC one cannot come to different conclusions about the same
    sidebar.
    """
    return parse_filters(pairs)


# ----------------------------------------------------------------------
# The catalogue. Public, read-only, the same for everybody.
# ----------------------------------------------------------------------


@router.get("/categories", response=list[CategoryNodeOut], summary="List the category tree")
def list_categories(request: HttpRequest, counts: bool = True) -> list[dict]:
    """Every visible category, nested, each with how many products are directly in it."""
    return shop_service.categories(with_counts=counts)


@router.get("/categories/{slug}", response=CategoryOut, summary="Read one category")
def read_category(request: HttpRequest, slug: str) -> dict:
    """One category: its breadcrumb, its children, and the attributes its products answer.

    The attributes are the reason this is worth calling before a product list:
    they are what a filter sidebar is built from, and they are inherited from
    every category above this one.
    """
    with _answers():
        return shop_service.category(slug)


@router.get("/brands", response=list[BrandOut], summary="List every brand")
def list_brands(request: HttpRequest) -> list[dict]:
    return shop_service.brands()


@router.get("/sellers", response=list[SellerOut], summary="List every seller")
def list_sellers(request: HttpRequest) -> list[dict]:
    """Everybody selling in this shop, so a storefront can offer them as a filter."""
    return shop_service.sellers()


@router.get("/sellers/{slug}", response=SellerDetailOut, summary="Read one seller")
def read_seller(request: HttpRequest, slug: str) -> dict:
    """One seller, and how much of the catalogue they carry."""
    with _answers():
        return shop_service.seller(slug)


@router.get("/products", response=ProductPageOut, summary="Search and filter the catalogue")
def list_products(
    request: HttpRequest,
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
    attribute: list[str] = Query([]),  # noqa: B008 - Ninja needs this request marker here.
    sort: str = "relevance",
    limit: int | None = None,
    offset: int = 0,
) -> dict:
    """One endpoint for search, for a category page, and for every filter on it.

    ``category`` includes everything underneath it, so asking for `kitchen`
    finds a knife filed under `kitchen/cutlery`. ``seller`` means "everything I
    can buy from them", which is what they sell directly and what they offer on
    somebody else's listing. ``attribute`` is repeatable and reads `code:value`
    -- `?attribute=colour:Red&attribute=size:M` -- and every one given has to
    match.

    ``sort`` is one of `relevance`, `newest`, `oldest`, `price_low`,
    `price_high`, `rating`, `popular`, `bestselling`, `name`. The two price
    sorts order by the shop's price rather than the discounted one: sorting by
    a price that depends on the clock would mean computing it for the whole
    catalogue before the database could sort it.
    """
    with _answers():
        return shop_service.products(
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
            attributes=_attribute_filters(attribute),
            sort=sort,
            limit=limit,
            offset=offset,
        )


@router.get("/listings", response=list[ListingSummaryOut], summary="List the named lists")
def list_listings(request: HttpRequest) -> list[dict]:
    """What lists this shop publishes, so a front page does not hard-code the keys."""
    return shop_service.listings()


@router.get("/listings/{key}", response=ListingOut, summary="Read one named list")
def read_listing(request: HttpRequest, key: str, limit: int | None = None, offset: int = 0) -> dict:
    """Best sellers, most popular, top rated, new arrivals, featured, on sale, in stock.

    Each is a filter and an ordering over the live catalogue rather than a
    stored list, so none of them can go stale.
    """
    with _answers():
        return shop_service.listing(key, limit=limit, offset=offset)


@router.get("/collections", response=list[CollectionOut], summary="List curated collections")
def list_collections(request: HttpRequest) -> list[dict]:
    """The lists somebody arranged by hand. Products are on the detail route."""
    return shop_service.collections()


@router.get("/collections/{slug}", response=CollectionOut, summary="Read one collection")
def read_collection(request: HttpRequest, slug: str, limit: int | None = None) -> dict:
    with _answers():
        return shop_service.collection(slug, limit=limit)


@router.get("/products/{slug}", response=ProductOut, summary="Read one product")
def read_product(request: HttpRequest, slug: str) -> dict:
    """Everything on a product page: images, variants, specs, price and rating.

    Answers for an archived product as well as a live one -- somebody's
    bookmark and somebody's review both point here -- and carries this caller's
    own like and review when a credential is offered.
    """
    with _answers():
        return shop_service.product(slug, _caller(request))


@router.post("/products/{slug}/view", response=ViewOut, summary="Record that somebody looked")
def record_view(request: HttpRequest, slug: str) -> dict:
    """Increment the view counter that the `popular` orderings are built on.

    Its own call rather than a side effect of reading the product, because a
    page served from a cache still wants to count the view and a crawler reading
    the JSON does not.
    """
    with _answers():
        return {"product": slug, "view_count": shop_service.view(slug)}


@router.get(
    "/products/{slug}/related",
    response=list[ProductSummaryOut],
    summary="Other products like this one",
)
def read_related(request: HttpRequest, slug: str, limit: int = 8) -> list[dict]:
    with _answers():
        return shop_service.related(slug, limit=limit)


@router.get("/products/{slug}/reviews", response=ReviewPageOut, summary="Read a product's reviews")
def list_reviews(
    request: HttpRequest, slug: str, limit: int | None = None, offset: int = 0
) -> dict:
    """The published reviews, newest first, with the rating they add up to.

    A review waiting for a moderator is not here. Its author can see it on
    `/shop/reviews/mine`, which is the one place it appears.
    """
    with _answers():
        return shop_service.reviews(slug, limit=limit, offset=offset)


# ----------------------------------------------------------------------
# The shopper's half. A credential, and no way to name another account.
# ----------------------------------------------------------------------


@router.post(
    "/products/{slug}/reviews",
    response=ReviewOut,
    auth=api_auth,
    summary="Write or replace your review",
)
def write_review(request: HttpRequest, slug: str, payload: ReviewIn) -> dict:
    """One review per account, so writing a second one edits the first.

    Where moderation is on -- it is, unless the shop turned it off -- the review
    comes back `pending` and nobody else can see it yet. An edit goes back
    through the queue, which is exactly the trick the queue is there to catch.
    """
    with _answers():
        return shop_service.review_product(
            request.user, slug, rating=payload.rating, title=payload.title, body=payload.body
        )


@router.delete(
    "/products/{slug}/reviews",
    response=ReviewRemovedOut,
    auth=api_auth,
    summary="Take back your review",
)
def remove_review(request: HttpRequest, slug: str) -> dict:
    with _answers():
        return shop_service.delete_review(request.user, slug)


@router.put("/products/{slug}/like", response=LikeOut, auth=api_auth, summary="Like a product")
def like_product(request: HttpRequest, slug: str) -> dict:
    """Idempotent: liking twice is not an error, it is the same answer."""
    with _answers():
        return shop_service.like(request.user, slug)


@router.delete("/products/{slug}/like", response=LikeOut, auth=api_auth, summary="Unlike a product")
def unlike_product(request: HttpRequest, slug: str) -> dict:
    with _answers():
        return shop_service.unlike(request.user, slug)


@router.get(
    "/favourites", response=ProductPageOut, auth=api_auth, summary="Everything you have liked"
)
def list_favourites(request: HttpRequest, limit: int | None = None, offset: int = 0) -> dict:
    return shop_service.liked(request.user, limit=limit, offset=offset)


@router.get(
    "/reviews/mine",
    response=MyReviewPageOut,
    auth=api_auth,
    summary="Everything you have reviewed",
)
def list_own_reviews(request: HttpRequest, limit: int | None = None, offset: int = 0) -> dict:
    """Including what is still waiting for a moderator: you are the one person
    entitled to know it exists."""
    return shop_service.my_reviews(request.user, limit=limit, offset=offset)


@router.get("/cart", response=CartOut, auth=api_auth, summary="Read your basket")
def read_cart(request: HttpRequest) -> dict:
    """Priced now, not when things were added -- so a sale that ended is over.

    The basket is created the first time it is read, so this never 404s.
    """
    return shop_service.cart(request.user)


@router.post("/cart/items", response=CartOut, auth=api_auth, summary="Add to your basket")
def add_to_cart(request: HttpRequest, payload: CartAddIn) -> dict:
    """Adding what is already there increases the quantity rather than refusing."""
    with _answers():
        return shop_service.add_to_cart(
            request.user,
            payload.product,
            variant_id=payload.variant,
            offer_id=payload.offer,
            quantity=payload.quantity,
        )


@router.patch(
    "/cart/items/{item_id}", response=CartOut, auth=api_auth, summary="Change a line's quantity"
)
def set_cart_quantity(request: HttpRequest, item_id: str, payload: CartQuantityIn) -> dict:
    """Zero removes the line, which is what a stepper does when it goes below one."""
    with _answers():
        return shop_service.set_cart_quantity(request.user, item_id, payload.quantity)


@router.delete("/cart/items/{item_id}", response=CartOut, auth=api_auth, summary="Remove a line")
def remove_from_cart(request: HttpRequest, item_id: str) -> dict:
    with _answers():
        return shop_service.remove_from_cart(request.user, item_id)


@router.delete("/cart", response=CartOut, auth=api_auth, summary="Empty your basket")
def clear_cart(request: HttpRequest) -> dict:
    return shop_service.clear_cart(request.user)


# ----------------------------------------------------------------------
# What a checkout needs first: an address to send it to, a way to get it
# there, and whatever code the shopper is holding.
# ----------------------------------------------------------------------


@router.get(
    "/addresses", response=list[AddressOut], auth=api_auth, summary="List your saved addresses"
)
def list_addresses(request: HttpRequest) -> list[dict]:
    """This account's address book, the default one first."""
    return shop_service.addresses(request.user)


@router.post("/addresses", response=AddressOut, auth=api_auth, summary="Save a delivery address")
def add_address(request: HttpRequest, payload: AddressIn) -> dict:
    """Save somewhere to send an order. The first one saved becomes the default."""
    with _answers():
        return shop_service.add_address(request.user, **payload.dict())


@router.get(
    "/addresses/{address_id}", response=AddressOut, auth=api_auth, summary="Read one address"
)
def read_address(request: HttpRequest, address_id: str) -> dict:
    with _answers():
        return shop_service.address(request.user, address_id)


@router.patch(
    "/addresses/{address_id}", response=AddressOut, auth=api_auth, summary="Edit an address"
)
def update_address(request: HttpRequest, address_id: str, payload: AddressPatchIn) -> dict:
    """Change part of a saved address.

    Editing one never rewrites an order that has already been placed: an order
    carries a flat copy of the address it was sent to, taken at that moment.
    """
    with _answers():
        return shop_service.update_address(
            request.user, address_id, **payload.dict(exclude_unset=True)
        )


@router.put(
    "/addresses/{address_id}/default",
    response=AddressOut,
    auth=api_auth,
    summary="Choose the default address",
)
def set_default_address(request: HttpRequest, address_id: str) -> dict:
    """Choosing one unchooses the last, so "the default" always means one address."""
    with _answers():
        return shop_service.set_default_address(request.user, address_id)


@router.delete(
    "/addresses/{address_id}",
    response=AddressRemovedOut,
    auth=api_auth,
    summary="Forget an address",
)
def remove_address(request: HttpRequest, address_id: str) -> dict:
    with _answers():
        return shop_service.remove_address(request.user, address_id)


@router.get("/shipping-methods", response=list[ShippingMethodOut], summary="List delivery options")
def list_shipping_methods(request: HttpRequest) -> list[dict]:
    """Every way an order can be delivered, costed against the caller's basket.

    Public, because a shopper comparing delivery options has not necessarily
    signed in yet -- but a signed-in caller gets each option costed for what is
    actually in their basket, which is the only way "free over 50" can be
    printed honestly.
    """
    return shop_service.shipping_methods(_caller(request))


@router.post("/cart/coupon", response=CouponPreviewOut, auth=api_auth, summary="Try a coupon code")
def preview_coupon(request: HttpRequest, payload: CouponPreviewIn) -> dict:
    """What a code would take off this basket, without committing to it.

    A code the shop will not accept comes back as an answer saying why, not as
    an error: this is what a checkout page asks while somebody is still typing,
    and a 400 per keystroke is not a thing a client should have to handle.
    """
    with _answers():
        return shop_service.preview_coupon(request.user, payload.code)


def _order_out(order: Any) -> dict:
    """One order in the shape every order endpoint answers with.

    Read through the service rather than off the row here, so the order a
    checkout returns is byte for byte the one reading it back gives -- a client
    that has to parse two shapes for the same thing eventually parses one of
    them wrong.
    """
    return shop_service.order(order.user, order.number)


@router.post("/checkout", response=OrderOut, auth=api_auth, summary="Create a pending order")
def checkout(request: HttpRequest, payload: CheckoutIn) -> dict:
    """Turn the caller's basket into an order, an invoice and a payment to settle.

    Stock is taken off the shelf here rather than when the money arrives, so two
    shoppers on this page for the last one cannot both succeed. The invoice is
    issued at the same moment, because it is the demand for payment.
    """
    with _answers():
        return _order_out(
            shop_service.checkout(
                request.user,
                address_id=payload.address,
                shipping_method_id=payload.shipping_method,
                coupon_code=payload.coupon,
                note=payload.note,
                provider=payload.provider,
            )
        )


@router.get("/orders", response=OrderPageOut, auth=api_auth, summary="List your orders")
def list_orders(request: HttpRequest, limit: int | None = None, offset: int = 0) -> dict:
    return shop_service.orders(request.user, limit=limit, offset=offset)


@router.get("/orders/{number}", response=OrderOut, auth=api_auth, summary="Read one of your orders")
def read_order(request: HttpRequest, number: str) -> dict:
    with _answers():
        return shop_service.order(request.user, number)


@router.get(
    "/orders/{number}/invoice",
    response=InvoiceOut,
    auth=api_auth,
    summary="Read an order's invoice",
)
def read_invoice(request: HttpRequest, number: str) -> dict:
    """The document issued when the order was placed, with the order on it."""
    with _answers():
        return shop_service.invoice(request.user, number)


@router.post(
    "/orders/{number}/payment/confirm",
    response=OrderOut,
    auth=api_auth,
    summary="Confirm a payment",
)
def confirm_payment(request: HttpRequest, number: str, payload: PaymentConfirmIn) -> dict:
    """The seam a payment provider's callback is pointed at.

    This starter wires up no gateway -- payments are created against the
    `manual` provider and settled by somebody in the admin looking at a bank
    statement. This endpoint is what a project points a real callback at once it
    has one, and it settles the order exactly the way the admin does.
    """
    with _answers():
        return _order_out(
            shop_service.confirm_payment(request.user, number, reference=payload.reference)
        )


@router.post(
    "/orders/{number}/cancel", response=OrderOut, auth=api_auth, summary="Cancel an unpaid order"
)
def cancel_order(request: HttpRequest, number: str) -> dict:
    with _answers():
        return _order_out(shop_service.cancel_order(request.user, number))
