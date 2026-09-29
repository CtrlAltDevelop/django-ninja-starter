"""What the shop emits, wired to the signals it already sends.

Registered only when ``apps.shop`` is installed. Nothing here reaches into the
shop's services and the shop never imports the club: the shop announces an order
paid or a review published after its transaction commits, and this bridge turns
each announcement into an occurrence -- the same shape as the wallet's bridge.
"""

from typing import Any

from apps.club.events import EventSpec, register

register(
    EventSpec(
        key="shop.order.paid",
        label="An order was paid for",
        description="Sent once per order, when its payment settles.",
        value_label="The order total, in the shop's currency",
        fields={
            "order_id": "The order's id",
            "currency": "The currency it was priced in",
            "items": "How many lines it had",
        },
        source="shop",
    )
)

register(
    EventSpec(
        key="shop.review.published",
        label="A review was published",
        description="Sent when a review clears moderation, not when it is written.",
        fields={"product_id": "What was reviewed", "rating": "1 to 5"},
        source="shop",
    )
)


def connect() -> None:
    """Listen to the shop's own signals. Called from the app's `ready`."""
    from apps.shop import signals as shop_signals

    shop_signals.order_paid.connect(_on_order_paid, dispatch_uid="club.shop.order_paid")
    shop_signals.review_published.connect(
        _on_review_published, dispatch_uid="club.shop.review_published"
    )


def _account(user_id: Any) -> Any:
    from django.contrib.auth import get_user_model

    return get_user_model().objects.filter(pk=user_id).first() if user_id else None


def _on_order_paid(sender: Any, **kwargs: Any) -> None:
    """One paid order, keyed on the order, so a replayed settlement pays nothing twice."""
    from apps.club import track

    order = kwargs.get("order") or {}
    user = _account(order.get("user_id"))
    if user is None:
        return
    track(
        user,
        "shop.order.paid",
        value=float(order.get("total") or 0),
        metadata={
            "order_id": str(order.get("id", "")),
            "currency": order.get("currency", ""),
            "items": order.get("items", 0),
        },
        reference=f"order:{order.get('id')}",
    )


def _on_review_published(sender: Any, **kwargs: Any) -> None:
    """One review, keyed on the review, so approving an edit again pays nothing twice."""
    from apps.club import track

    review = kwargs.get("review") or {}
    user = _account(review.get("user_id"))
    if user is None:
        return
    track(
        user,
        "shop.review.published",
        metadata={"product_id": str(review.get("product_id", "")), "rating": review.get("rating")},
        reference=f"review:{review.get('id')}",
    )
