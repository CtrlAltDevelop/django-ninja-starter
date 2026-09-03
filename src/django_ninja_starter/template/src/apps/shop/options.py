"""The handful of decisions a deployment makes about the shop.

Every one of them is read through a function with a default, never imported as a
constant, for the reason the rest of this app is written the way it is: the
package has to work when it is copied into a project that has never declared a
single ``SHOP_*`` setting. A missing setting is a default here, not an
``AttributeError`` at import time.

They are also read per call rather than captured at import, so a test -- or an
admin action, or a management command -- can override one with
``settings.override`` and have the answer change.
"""

from typing import Any

from django.conf import settings

DEFAULT_CURRENCY = "USD"
DEFAULT_MAX_ITEM_QUANTITY = 99
DEFAULT_PAGE_SIZE = 24
DEFAULT_MAX_PAGE_SIZE = 100


def _setting(name: str, fallback: Any) -> Any:
    value = getattr(settings, name, None)
    return fallback if value in (None, "") else value


def currency() -> str:
    """The ISO 4217 code every price in this catalogue is quoted in.

    One code for the whole shop rather than one per product: a catalogue whose
    prices are in several currencies needs a conversion policy, a rounding
    policy and a display policy, and inventing those silently is worse than
    saying the shop has one currency.
    """
    return str(_setting("SHOP_CURRENCY", DEFAULT_CURRENCY)).upper()


def review_moderation() -> bool:
    """Whether a review is held for a moderator before anybody else can read it.

    On by default. A storefront that publishes whatever is typed into it is a
    spam target from the first week, and the shop owner who wants the other
    behaviour is in a position to ask for it.
    """
    return bool(_setting("SHOP_REVIEW_MODERATION", True))


def max_item_quantity() -> int:
    """The most of one product a single cart line may hold."""
    return max(1, int(_setting("SHOP_MAX_ITEM_QUANTITY", DEFAULT_MAX_ITEM_QUANTITY)))


def page_size() -> int:
    """How many rows a listing returns when the caller does not say."""
    return max(1, int(_setting("SHOP_PAGE_SIZE", DEFAULT_PAGE_SIZE)))


def max_page_size() -> int:
    """The ceiling on ``limit``, so one request cannot ask for the catalogue."""
    return max(page_size(), int(_setting("SHOP_MAX_PAGE_SIZE", DEFAULT_MAX_PAGE_SIZE)))


def bounded_page(limit: int | None, offset: int | None = 0) -> tuple[int, int]:
    """Clamp a caller's paging request into something the database should answer.

    Returns ``(limit, offset)``. A missing or nonsensical limit becomes the
    configured page size, an enormous one becomes the ceiling, and a negative
    offset becomes zero -- rather than any of the three being a 400 the client
    has to handle for what is almost always a typo.
    """
    size = page_size() if not limit or limit < 1 else min(int(limit), max_page_size())
    start = max(0, int(offset or 0))
    return size, start
