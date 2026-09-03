"""What a shopper pays, and which campaign decided it.

The two rules this module exists to enforce are that only one discount ever
applies and that nothing is stored, so most of what is below is about overlap
and about the clock.
"""

from decimal import Decimal
from typing import Any

import pytest
from django.test import override_settings
from django.utils import timezone

from apps.shop.models import Category, Discount, DiscountKind, Product
from apps.shop.pricing import discounted_ids, price_of, running_discounts

pytestmark = pytest.mark.django_db


def _campaign(name: str, **kwargs: Any) -> Discount:
    kwargs.setdefault("value", Decimal("10.00"))
    return Discount.objects.create(name=name, **kwargs)


class TestUndiscounted:
    def test_with_nothing_running_the_price_is_the_price(self, laptop: Product) -> None:
        price = price_of(laptop)

        assert price.amount == Decimal("1200.00")
        assert price.base_amount == Decimal("1200.00")
        assert not price.is_discounted

    def test_the_currency_is_the_shops_one(self, laptop: Product) -> None:
        with override_settings(SHOP_CURRENCY="gbp"):
            assert price_of(laptop).currency == "GBP"

    def test_a_variant_price_overrides_the_products(self, tshirt: Product) -> None:
        large = tshirt.variants.get(sku="TEE-L")

        assert price_of(tshirt, large).amount == Decimal("22.00")

    def test_a_variant_without_a_price_charges_the_products(self, medium: Any) -> None:
        assert price_of(medium.product, medium).amount == Decimal("20.00")


class TestOneCampaignApplies:
    def test_a_percentage_comes_off(self, laptop: Product, sale: Discount) -> None:
        price = price_of(laptop)

        assert price.amount == Decimal("1080.00")
        assert price.base_amount == Decimal("1200.00")
        assert price.discount is not None
        assert price.discount.percent_off == 10

    def test_the_campaign_that_made_the_price_is_named(
        self, laptop: Product, sale: Discount
    ) -> None:
        discount = price_of(laptop).discount

        assert discount is not None
        assert discount.name == "Spring sale"
        assert discount.amount_off == Decimal("120.00")
        assert discount.ends_at == sale.ends_at

    def test_a_cap_limits_a_percentage(self, laptop: Product) -> None:
        campaign = _campaign(
            "Capped", kind=DiscountKind.PERCENT, value=Decimal("50"), max_amount=Decimal("100")
        )
        campaign.products.add(laptop)

        assert price_of(laptop).amount == Decimal("1100.00")

    def test_an_amount_off_never_takes_the_price_below_nothing(self, tshirt: Product) -> None:
        campaign = _campaign("Generous", kind=DiscountKind.AMOUNT, value=Decimal("500"))
        campaign.products.add(tshirt)

        assert price_of(tshirt).amount == Decimal("0.00")

    def test_a_campaign_on_a_parent_category_reaches_the_product(
        self, electronics: Category, laptop: Product
    ) -> None:
        campaign = _campaign("Electronics week", value=Decimal("25"))
        campaign.categories.add(electronics)

        assert price_of(laptop).amount == Decimal("900.00")

    def test_a_campaign_on_something_else_leaves_this_alone(
        self, laptop: Product, tshirt: Product
    ) -> None:
        campaign = _campaign("Shirts only")
        campaign.products.add(tshirt)

        assert not price_of(laptop).is_discounted


class TestOnlyTheBestApplies:
    def test_two_overlapping_campaigns_do_not_stack(self, laptop: Product) -> None:
        """The alternative is the weekend a shop sells at a negative price."""
        small = _campaign("Small", value=Decimal("5"))
        big = _campaign("Big", value=Decimal("20"))
        small.products.add(laptop)
        big.products.add(laptop)

        price = price_of(laptop)

        assert price.amount == Decimal("960.00")
        assert price.discount is not None
        assert price.discount.name == "Big"

    def test_priority_settles_a_tie(self, laptop: Product) -> None:
        quiet = _campaign("Quiet", value=Decimal("10"), priority=0)
        loud = _campaign("Loud", value=Decimal("10"), priority=5)
        quiet.products.add(laptop)
        loud.products.add(laptop)

        discount = price_of(laptop).discount

        assert discount is not None
        assert discount.name == "Loud"

    def test_a_campaign_worth_nothing_never_wins(self, laptop: Product) -> None:
        """A cap of zero saves the shopper nothing, so it must not claim the price."""
        nothing = _campaign(
            "Nothing", kind=DiscountKind.PERCENT, value=Decimal("10"), max_amount=Decimal("0")
        )
        nothing.products.add(laptop)

        assert not price_of(laptop).is_discounted


class TestTheClock:
    def test_a_finished_campaign_stops_applying_on_its_own(self, laptop: Product) -> None:
        """Nothing is written when a sale ends, so nothing has to be undone."""
        finished = _campaign(
            "Last month",
            starts_at=timezone.now() - timezone.timedelta(days=30),
            ends_at=timezone.now() - timezone.timedelta(days=1),
        )
        finished.products.add(laptop)

        assert running_discounts() == []
        assert not price_of(laptop).is_discounted

    def test_a_campaign_that_has_not_started_does_not_apply_yet(self, laptop: Product) -> None:
        later = _campaign("Next week", starts_at=timezone.now() + timezone.timedelta(days=7))
        later.products.add(laptop)

        assert not price_of(laptop).is_discounted

    def test_a_campaign_turned_off_stops_applying(self, laptop: Product, sale: Discount) -> None:
        Discount.objects.filter(pk=sale.pk).update(is_active=False)

        assert not price_of(laptop).is_discounted


class TestWhatIsOnSale:
    def test_it_names_the_products_a_campaign_reaches(
        self, laptop: Product, tshirt: Product, sale: Discount
    ) -> None:
        assert discounted_ids([laptop, tshirt]) == {laptop.pk}

    def test_with_no_campaigns_nothing_is_on_sale(self, laptop: Product, tshirt: Product) -> None:
        assert discounted_ids([laptop, tshirt]) == set()


class TestRounding:
    def test_a_price_comes_back_to_the_cent(self, tshirt: Product) -> None:
        """A third off 20.00 is 13.33, not 13.333333."""
        campaign = _campaign("A third", kind=DiscountKind.PERCENT, value=Decimal("33.33"))
        campaign.products.add(tshirt)

        price = price_of(tshirt)

        assert price.amount == Decimal("13.33")
        assert price.amount.as_tuple().exponent == -2
