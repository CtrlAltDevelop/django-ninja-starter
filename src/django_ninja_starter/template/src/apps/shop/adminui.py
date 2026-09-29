"""This app's own sidebar group and dashboard numbers.

Ordered the way a shopkeeper's day is rather than the way the models are: what
needs doing -- orders, payments, moderation -- above what is designed once and
edited rarely. See :mod:`infrastructure.common.adminui` for the protocol.
"""

from datetime import timedelta
from typing import Any

from django.db.models import Case, F, Q, Sum, When
from django.http import HttpRequest
from django.utils import timezone

from infrastructure.common.adminui import Section, card, changelist, item, may

NAVIGATION_ORDER = 40
DASHBOARD_ORDER = 40


def navigation(request: HttpRequest) -> dict[str, Any]:
    return {
        "title": "Shop",
        "separator": False,
        "collapsible": False,
        "items": [
            item("Orders", "receipt_long", changelist("shop", "order"), "shop.view_order"),
            item("Payments", "payments", changelist("shop", "payment"), "shop.view_payment"),
            item("Invoices", "description", changelist("shop", "invoice"), "shop.view_invoice"),
            item(
                "Products",
                "inventory_2",
                changelist("shop", "product"),
                "shop.view_product",
                "shop.change_product",
            ),
            item(
                "Categories", "account_tree", changelist("shop", "category"), "shop.view_category"
            ),
            item("Brands", "sell", changelist("shop", "brand"), "shop.view_brand"),
            item("Sellers", "storefront", changelist("shop", "seller"), "shop.view_seller"),
            item(
                "Collections",
                "collections_bookmark",
                changelist("shop", "collection"),
                "shop.view_collection",
            ),
            item("Discounts", "local_offer", changelist("shop", "discount"), "shop.view_discount"),
            item(
                "Coupons",
                "confirmation_number",
                changelist("shop", "coupon"),
                "shop.view_coupon",
            ),
            item(
                "Delivery options",
                "local_shipping",
                changelist("shop", "shippingmethod"),
                "shop.view_shippingmethod",
            ),
            item(
                "Reviews",
                "reviews",
                changelist("shop", "review"),
                "shop.view_review",
                "shop.change_review",
            ),
            item(
                "Stock held",
                "inventory",
                changelist("shop", "inventoryreservation"),
                "shop.view_inventoryreservation",
            ),
            item("Baskets", "shopping_cart", changelist("shop", "cart"), "shop.view_cart"),
        ],
    }


def dashboard(request: HttpRequest) -> Section | None:
    """The numbers a shopkeeper opens the admin to find out.

    Not "how many rows are there" but "what has to happen today". Money taken
    this week is the one that is a result rather than a task, and it counts
    orders that were actually paid -- a total over every row would include the
    baskets abandoned at the payment page and would flatter the shop every
    morning.
    """
    if not may(request, "shop.view_order", "shop.view_product"):
        return None
    from apps.shop.models import Order, OrderStatus, Product, ProductStatus, Review, ReviewStatus

    week_ago = timezone.now() - timedelta(days=7)
    settled = (
        OrderStatus.PAID,
        OrderStatus.PROCESSING,
        OrderStatus.SHIPPED,
        OrderStatus.COMPLETED,
    )
    paid_this_week = Order.objects.filter(status__in=settled, created_at__gte=week_ago)
    revenue = paid_this_week.aggregate(taken=Sum("total"))["taken"] or 0
    awaiting = Order.objects.filter(status=OrderStatus.PENDING).count()
    to_send = Order.objects.filter(status__in=(OrderStatus.PAID, OrderStatus.PROCESSING)).count()

    # Counted in the database rather than over `available_stock` in Python: the
    # dashboard runs on every admin page load, and a property per product is a
    # query per product.
    tracked = Product.objects.filter(status=ProductStatus.ACTIVE, track_inventory=True)
    on_hand = Case(
        When(has_variants=True, then=Sum("variants__stock", filter=Q(variants__is_active=True))),
        default=F("stock"),
    )
    counted = tracked.annotate(on_hand=on_hand)
    out_of_stock = counted.filter(
        Q(on_hand__lte=0) | Q(on_hand__isnull=True), allow_backorder=False
    ).count()
    low_stock = counted.filter(on_hand__gt=0, on_hand__lte=F("low_stock_threshold")).count()
    waiting = Review.objects.filter(status=ReviewStatus.PENDING).count()

    return Section(
        title="Shop",
        cards=[
            card(
                "Orders, last 7 days",
                paid_this_week.count(),
                hint=f"{revenue} taken",
                icon="receipt_long",
                link=changelist("shop", "order"),
            ),
            card(
                "Waiting to go out",
                to_send,
                hint=f"{awaiting} still awaiting payment"
                if awaiting
                else "nothing is awaiting payment",
                icon="local_shipping",
                link=changelist("shop", "order"),
                tone="warn" if to_send else "",
            ),
            card(
                "Out of stock",
                out_of_stock,
                hint=f"{low_stock} more running low"
                if low_stock
                else "nothing else is running low",
                icon="inventory_2",
                link=changelist("shop", "product"),
                tone="bad" if out_of_stock else "good",
            ),
            card(
                "Reviews to moderate",
                waiting,
                hint="nobody can read these yet" if waiting else "the queue is empty",
                icon="reviews",
                link=changelist("shop", "review"),
                tone="warn" if waiting else "",
            ),
        ],
    )
