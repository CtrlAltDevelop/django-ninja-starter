"""Which size, in which colour, from which seller.

A product sold in variants asks three questions at once, and the product page
has to answer all three in one document: what a shopper can pick, which of those
picks are still buyable, and who is holding them. Two of those are not facts
about a variant row -- they are facts about every seller of it -- so what is
tested here is mostly the arithmetic that joins the two tables up.

The category is what decides the axes. A shirt has sizes because ``Shirts``
declares a size attribute, and a laptop has colours because ``Laptops`` declares
a colour one, which is the whole reason a size that means "42" and a size that
means "L" never meet.
"""

from decimal import Decimal
from typing import Any

import pytest
from django.core.exceptions import ValidationError
from django.test import Client

from apps.shop.attributes import AttributeType
from apps.shop.models import (
    Category,
    CategoryAttribute,
    Product,
    ProductOffer,
    ProductVariant,
    Seller,
)
from apps.shop.pricing import can_fill, is_available, shelf, total_stock

pytestmark = pytest.mark.django_db

SHOP = "/api/v1/shop"


@pytest.fixture
def client() -> Client:
    return Client()


def data(response: Any) -> Any:
    assert response.status_code == 200, response.content
    return response.json()["data"]


def page(client: Client) -> Any:
    return data(client.get(f"{SHOP}/products/plain-tee"))


def sizes(product: Any) -> Any:
    """The one variant axis a shirt has, as the page publishes it."""
    [axis] = product["variant_attributes"]
    return axis


@pytest.fixture
def two_axes(shirts: Category, tshirt: Product) -> Product:
    """The same shirt in two colours as well as two sizes.

    Four variants across two axes, which is where a picker stops being a list
    and starts being a grid: pressing "Red" has to narrow the sizes, and that
    is only possible if each value names the variants that answer it.
    """
    CategoryAttribute.objects.create(
        category=shirts,
        name="Colour",
        code="colour",
        attribute_type=AttributeType.COLOR,
        is_variant=True,
        order=1,
    )
    for variant in tshirt.variants.all():
        variant.options = {**variant.options, "colour": "#ff0000"}
        variant.save()
    for size, stock in (("M", 2), ("L", 1)):
        ProductVariant.objects.create(
            product=tshirt,
            sku=f"TEE-{size}-BLUE",
            options={"size": size, "colour": "#0000ff"},
            stock=stock,
            order=2,
        )
    return tshirt


@pytest.fixture
def large_elsewhere(tshirt: Product, resellers: Seller) -> ProductOffer:
    """Somebody else holding the size the shop itself has sold out of."""
    return ProductOffer.objects.create(
        product=tshirt,
        seller=resellers,
        variant=tshirt.variants.get(sku="TEE-L"),
        price=Decimal("21.00"),
        stock=4,
        lead_time_days=1,
    )


class TestOnlyAFiniteAxisCanDistinguishVariants:
    def test_a_choice_can(self, shirts: Category) -> None:
        attribute = CategoryAttribute(
            category=shirts,
            name="Fit",
            code="fit",
            attribute_type=AttributeType.CHOICE,
            choices=["Slim", "Regular"],
            is_variant=True,
        )

        attribute.full_clean()

    def test_free_text_cannot(self, shirts: Category) -> None:
        """Two spellings of one option are two variants, two shelves and one
        picker offering the same thing twice."""
        attribute = CategoryAttribute(
            category=shirts,
            name="Fit",
            code="fit",
            attribute_type=AttributeType.TEXT,
            is_variant=True,
        )

        with pytest.raises(ValidationError) as refused:
            attribute.full_clean()

        assert "is_variant" in refused.value.message_dict


class TestOneRuleDecidesWhatIsAvailable:
    def test_the_shelf_is_the_most_specific_row(
        self, tshirt: Product, large_elsewhere: ProductOffer
    ) -> None:
        medium = tshirt.variants.get(sku="TEE-M")

        assert shelf(tshirt, medium) == 3
        assert shelf(tshirt, None, large_elsewhere) == 4

    def test_a_sold_out_variant_is_available_when_a_seller_has_it(
        self, tshirt: Product, large_elsewhere: ProductOffer
    ) -> None:
        large = tshirt.variants.get(sku="TEE-L")

        assert not can_fill(tshirt, large)
        assert is_available(tshirt, large)

    def test_a_variant_nobody_has_is_not_available(self, tshirt: Product) -> None:
        assert not is_available(tshirt, tshirt.variants.get(sku="TEE-L"))

    def test_a_backordered_product_can_always_be_filled(self, tshirt: Product) -> None:
        Product.objects.filter(pk=tshirt.pk).update(allow_backorder=True)
        tshirt.refresh_from_db()

        assert is_available(tshirt, tshirt.variants.get(sku="TEE-L"))

    def test_the_total_counts_every_seller(
        self, tshirt: Product, large_elsewhere: ProductOffer
    ) -> None:
        large = tshirt.variants.get(sku="TEE-L")

        assert total_stock(tshirt, large) == 4


class TestAVariantCarriesItsSellers:
    def test_the_page_lists_the_sellers_of_every_size(
        self, client: Client, tshirt: Product, large_elsewhere: ProductOffer
    ) -> None:
        """A variant product has no offer whose variant is empty, so a page that
        asked only for those would say nobody else sells any of it."""
        product = page(client)

        assert [offer["seller"]["slug"] for offer in product["offers"]] == ["bargain-bin"]

    def test_each_seller_is_filed_under_the_size_they_sell(
        self, client: Client, tshirt: Product, large_elsewhere: ProductOffer
    ) -> None:
        medium, large = page(client)["variants"]

        assert medium["sellers"] == []
        assert [offer["seller"]["slug"] for offer in large["sellers"]] == ["bargain-bin"]
        assert large["sellers"][0]["price"]["amount"] == "21.00"

    def test_a_size_the_shop_sold_out_of_is_still_in_stock_elsewhere(
        self, client: Client, tshirt: Product, large_elsewhere: ProductOffer
    ) -> None:
        _, large = page(client)["variants"]

        assert large["stock"] == 0
        assert large["total_stock"] == 4
        assert large["in_stock"] is True
        assert large["seller_count"] == 1

    def test_a_size_nobody_has_says_so(self, client: Client, tshirt: Product) -> None:
        _, large = page(client)["variants"]

        assert large["total_stock"] == 0
        assert large["in_stock"] is False
        assert large["seller_count"] == 0

    def test_the_shop_itself_counts_as_a_seller(self, client: Client, tshirt: Product) -> None:
        medium, _ = page(client)["variants"]

        assert medium["stock"] == 3
        assert medium["total_stock"] == 3
        assert medium["seller_count"] == 1


class TestThePickerKnowsWhatIsLeft:
    def test_the_axis_is_the_categorys_own_attribute(self, client: Client, tshirt: Product) -> None:
        axis = sizes(page(client))

        assert axis["code"] == "size"
        assert axis["is_variant"] is True

    def test_only_the_values_that_exist_are_offered(self, client: Client, tshirt: Product) -> None:
        """The category allows three sizes and this shirt is made in two."""
        axis = sizes(page(client))

        assert axis["choices"] == ["S", "M", "L"]
        assert [value["value"] for value in axis["values"]] == ["M", "L"]

    def test_the_values_follow_the_declared_order(self, client: Client, tshirt: Product) -> None:
        """Added M then L, declared S, M, L -- so the picker prints M then L
        whichever order somebody happened to type them in."""
        tshirt.variants.filter(sku="TEE-M").update(order=5)

        assert [value["value"] for value in sizes(page(client))["values"]] == ["M", "L"]

    def test_a_sold_out_value_can_be_greyed_out(self, client: Client, tshirt: Product) -> None:
        medium, large = sizes(page(client))["values"]

        assert medium["in_stock"] is True
        assert large["in_stock"] is False

    def test_a_value_names_the_variants_that_answer_it(
        self, client: Client, tshirt: Product
    ) -> None:
        product = page(client)
        by_sku = {variant["sku"]: variant["id"] for variant in product["variants"]}

        assert sizes(product)["values"][0]["variants"] == [by_sku["TEE-M"]]

    def test_a_seller_puts_a_value_back_in_stock(
        self, client: Client, tshirt: Product, large_elsewhere: ProductOffer
    ) -> None:
        _, large = sizes(page(client))["values"]

        assert large["in_stock"] is True

    def test_a_product_with_no_variants_offers_no_values(
        self, client: Client, laptop: Product
    ) -> None:
        """Its category declares a colour, and this laptop comes in none of
        them: the axis is still named, with nothing to press."""
        product = data(client.get(f"{SHOP}/products/featherbook-14"))

        assert [axis["code"] for axis in product["variant_attributes"]] == ["colour"]
        assert product["variant_attributes"][0]["values"] == []
        assert product["variants"] == []


class TestMoreThanOneAxis:
    def test_a_colour_can_distinguish_variants(self, two_axes: Product) -> None:
        assert two_axes.variants.count() == 4

    def test_each_axis_lists_only_its_own_values(self, client: Client, two_axes: Product) -> None:
        axes = {axis["code"]: axis for axis in page(client)["variant_attributes"]}

        assert [value["value"] for value in axes["size"]["values"]] == ["M", "L"]
        # Uppercased on the way in, so one colour is one option rather than two.
        assert [value["value"] for value in axes["colour"]["values"]] == ["#FF0000", "#0000FF"]

    def test_a_value_is_in_stock_when_any_variant_carrying_it_is(
        self, client: Client, two_axes: Product
    ) -> None:
        """Red comes in a medium with three on the shelf and a large with none,
        so red itself is still pressable."""
        axes = {axis["code"]: axis for axis in page(client)["variant_attributes"]}

        assert [value["in_stock"] for value in axes["colour"]["values"]] == [True, True]
        assert [value["in_stock"] for value in axes["size"]["values"]] == [True, True]

    def test_a_value_names_every_variant_that_answers_it(
        self, client: Client, two_axes: Product
    ) -> None:
        """What lets a picker narrow the second axis once the first is pressed."""
        product = page(client)
        by_id = {variant["id"]: variant["sku"] for variant in product["variants"]}
        axes = {axis["code"]: axis for axis in product["variant_attributes"]}

        red = axes["colour"]["values"][0]
        assert sorted(by_id[id] for id in red["variants"]) == ["TEE-L", "TEE-M"]

    def test_an_undeclared_value_sorts_last_rather_than_disappearing(
        self, client: Client, tshirt: Product
    ) -> None:
        """A colour has no declared list to sort by, and a shop that narrowed a
        choice list after somebody made variants must not lose the odd one."""
        size = tshirt.category.attributes.get(code="size")
        size.choices = ["S", "L"]
        size.save()

        assert [value["value"] for value in sizes(page(client))["values"]] == ["L", "M"]


class TestOnlyLiveRowsCount:
    def test_a_retired_variant_is_not_offered(self, client: Client, tshirt: Product) -> None:
        tshirt.variants.filter(sku="TEE-L").update(is_active=False)

        product = page(client)

        assert [variant["sku"] for variant in product["variants"]] == ["TEE-M"]
        assert [value["value"] for value in sizes(product)["values"]] == ["M"]

    def test_a_hidden_offer_does_not_stock_a_variant(
        self, client: Client, tshirt: Product, large_elsewhere: ProductOffer
    ) -> None:
        ProductOffer.objects.filter(pk=large_elsewhere.pk).update(is_active=False)

        _, large = page(client)["variants"]

        assert large["sellers"] == []
        assert large["total_stock"] == 0
        assert large["in_stock"] is False

    def test_a_hidden_seller_does_not_either(
        self, client: Client, tshirt: Product, resellers: Seller, large_elsewhere: ProductOffer
    ) -> None:
        Seller.objects.filter(pk=resellers.pk).update(is_active=False)

        _, large = page(client)["variants"]

        assert large["seller_count"] == 0
        assert large["in_stock"] is False

    def test_an_empty_offer_is_not_a_seller_of_that_size(
        self, client: Client, tshirt: Product, large_elsewhere: ProductOffer
    ) -> None:
        ProductOffer.objects.filter(pk=large_elsewhere.pk).update(stock=0)

        _, large = page(client)["variants"]

        assert large["sellers"] == []
        assert large["in_stock"] is False
