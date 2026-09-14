"""What the shop emits, declared without importing the shop.

Registered only when ``apps.shop`` is installed. The keys are declarations, not
imports: nothing here reaches into the shop, and the shop reaches the club
through :func:`apps.club.track` at its own call sites.
"""

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
