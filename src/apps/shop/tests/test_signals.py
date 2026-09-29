"""What the shop announces, and when: after commit, and once per thing that happened.

These are what another app -- the club's bridge, or one of your own -- builds on,
so the property worth asserting is the count. An order announced twice is an
order some listener pays for twice.
"""

from typing import Any

import pytest
from django.contrib import admin
from django.test import RequestFactory

from apps.shop import signals
from apps.shop.models import Address, Order, Product, Review, ShippingMethod
from apps.shop.services import shop_service

pytestmark = pytest.mark.django_db


class Heard:
    """Collects what a signal carried, so a test can assert on the payload."""

    def __init__(self, signal: Any) -> None:
        self.signal = signal
        self.payloads: list[dict[str, Any]] = []

    def __enter__(self) -> "Heard":
        self.signal.connect(self._receive)
        return self

    def __exit__(self, *_: Any) -> None:
        self.signal.disconnect(self._receive)

    def _receive(self, sender: Any, **kwargs: Any) -> None:
        self.payloads.append(kwargs)


@pytest.fixture
def placed(alice: Any, laptop: Product, address: Address, shipping: ShippingMethod) -> Order:
    shop_service.add_to_cart(alice, "featherbook-14", quantity=2)
    return shop_service.checkout(alice, address_id=address.pk, shipping_method_id=shipping.pk)


def test_settling_an_order_announces_it_once(
    placed: Order, alice: Any, django_capture_on_commit_callbacks: Any
) -> None:
    """A gateway retrying its callback settles the same order, and says so once."""
    with (
        Heard(signals.order_paid) as heard,
        django_capture_on_commit_callbacks(execute=True),
    ):
        shop_service.settle_order(placed)
        shop_service.settle_order(placed)

    assert len(heard.payloads) == 1
    order = heard.payloads[0]["order"]
    assert order["number"] == placed.number
    assert order["user_id"] == alice.pk
    assert order["total"] == placed.total
    assert order["items"] == placed.items.count()


def test_a_review_published_on_arrival_is_announced(
    alice: Any, laptop: Product, django_capture_on_commit_callbacks: Any
) -> None:
    with (
        Heard(signals.review_published) as heard,
        django_capture_on_commit_callbacks(execute=True),
    ):
        shop_service.review_product(alice, laptop.slug, rating=5)

    assert heard.payloads[0]["review"]["rating"] == 5


def test_a_moderated_review_is_announced_when_approved_and_not_before(
    alice: Any, laptop: Product, settings: Any, django_capture_on_commit_callbacks: Any
) -> None:
    """Approval is a queryset update, which sends no `post_save` for anybody to hear."""
    settings.SHOP_REVIEW_MODERATION = True
    screen = admin.site._registry[Review]

    with (
        Heard(signals.review_published) as heard,
        django_capture_on_commit_callbacks(execute=True),
    ):
        shop_service.review_product(alice, laptop.slug, rating=4)
        assert heard.payloads == []
        for _ in range(2):
            screen._moderate(
                RequestFactory().post("/"), Review.objects.filter(user=alice), "approved"
            )

    assert len(heard.payloads) == 1
    assert heard.payloads[0]["review"]["user_id"] == alice.pk
