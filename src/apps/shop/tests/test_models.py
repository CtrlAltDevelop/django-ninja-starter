"""What the tables refuse, and what they work out for themselves.

Every write in this app goes through ``full_clean``, so a rule stated in
``clean`` is a rule an import, a data migration and a shell session obey too --
which is the reason these are tests of the model rather than of a form.
"""

from decimal import Decimal
from typing import Any

import pytest
from django.core.exceptions import ValidationError
from django.utils import timezone

from apps.shop.attributes import AttributeType
from apps.shop.models import (
    Category,
    CategoryAttribute,
    Discount,
    DiscountKind,
    Product,
    ProductAttribute,
    ProductStatus,
    ProductVariant,
    Review,
    ReviewStatus,
    ShippingMethod,
    refresh_review_stats,
)

pytestmark = pytest.mark.django_db


class TestCategoryTree:
    def test_a_category_cannot_be_its_own_parent(self, electronics: Category) -> None:
        electronics.parent = electronics

        with pytest.raises(ValidationError):
            electronics.save()

    def test_a_cycle_is_refused_however_long_it_is(
        self, electronics: Category, laptops: Category
    ) -> None:
        electronics.parent = laptops

        with pytest.raises(ValidationError):
            electronics.save()

    def test_ancestors_read_from_the_root_down(self, laptops: Category) -> None:
        assert [category.slug for category in laptops.ancestors(including_self=True)] == [
            "electronics",
            "laptops",
        ]

    def test_a_branch_is_the_category_and_everything_under_it(
        self, electronics: Category, laptops: Category
    ) -> None:
        assert set(electronics.branch_ids()) == {electronics.pk, laptops.pk}

    def test_the_attribute_schema_inherits_from_the_categories_above(
        self, laptops: Category
    ) -> None:
        """A laptop answers "warranty" because electronics declared it."""
        codes = [attribute.code for attribute in laptops.attribute_schema()]

        assert codes == ["warranty", "screen-size", "colour"]

    def test_a_child_overrides_an_inherited_attribute_with_the_same_code(
        self, electronics: Category, laptops: Category
    ) -> None:
        CategoryAttribute.objects.create(
            category=laptops, name="Warranty", code="warranty", unit="years"
        )
        schema = {attribute.code: attribute for attribute in laptops.attribute_schema()}

        assert schema["warranty"].unit == "years"


class TestCategoryAttribute:
    def test_a_choice_attribute_has_to_offer_choices(self, laptops: Category) -> None:
        attribute = CategoryAttribute(
            category=laptops, name="Finish", code="finish", attribute_type=AttributeType.CHOICE
        )

        with pytest.raises(ValidationError):
            attribute.save()

    def test_a_value_outside_the_choices_is_refused(
        self, laptop: Product, laptops: Category
    ) -> None:
        attribute = CategoryAttribute.objects.create(
            category=laptops,
            name="Finish",
            code="finish",
            attribute_type=AttributeType.CHOICE,
            choices=["Matte", "Gloss"],
        )
        answer = ProductAttribute(product=laptop, attribute=attribute, value="Sparkly")

        with pytest.raises(ValidationError):
            answer.save()

    def test_a_number_arrives_as_a_number_however_it_was_typed(
        self, laptop: Product, electronics: Category
    ) -> None:
        answered = ProductAttribute.objects.create(
            product=laptop, attribute=electronics.attributes.get(code="warranty"), value="24"
        )

        assert answered.value == 24

    def test_a_product_cannot_answer_an_attribute_that_varies_per_variant(
        self, laptop: Product, laptops: Category
    ) -> None:
        answer = ProductAttribute(
            product=laptop, attribute=laptops.attributes.get(code="colour"), value="Silver"
        )

        with pytest.raises(ValidationError):
            answer.save()

    def test_a_product_cannot_answer_another_categorys_attribute(
        self, laptop: Product, shirts: Category
    ) -> None:
        foreign = CategoryAttribute.objects.create(category=shirts, name="Fit", code="fit")
        answer = ProductAttribute(product=laptop, attribute=foreign, value="Regular")

        with pytest.raises(ValidationError):
            answer.save()


class TestProduct:
    def test_a_struck_through_price_below_the_price_is_refused(self, laptop: Product) -> None:
        """That is a sale, and a sale has dates."""
        laptop.compare_at_price = Decimal("900.00")

        with pytest.raises(ValidationError):
            laptop.save()

    def test_a_product_with_variants_carries_no_stock_of_its_own(self, tshirt: Product) -> None:
        tshirt.stock = 4

        with pytest.raises(ValidationError):
            tshirt.save()

    def test_stock_counts_the_variants_when_there_are_variants(self, tshirt: Product) -> None:
        assert tshirt.available_stock == 3

    def test_something_untracked_is_always_in_stock(self, laptop: Product) -> None:
        laptop.track_inventory = False
        laptop.stock = 0
        laptop.save()

        assert laptop.in_stock

    def test_a_backorder_is_in_stock_with_nothing_on_the_shelf(self, laptop: Product) -> None:
        laptop.stock = 0
        laptop.allow_backorder = True
        laptop.save()

        assert laptop.in_stock

    def test_a_draft_is_not_live(self, draft: Product) -> None:
        assert not draft.is_live

    def test_a_publication_date_in_the_future_is_not_live_yet(self, laptop: Product) -> None:
        laptop.published_at = timezone.now() + timezone.timedelta(days=1)
        laptop.save()

        assert not laptop.is_live

    def test_a_required_attribute_nobody_answered_is_reported_missing(
        self, tshirt: Product, shirts: Category
    ) -> None:
        CategoryAttribute.objects.create(
            category=shirts, name="Fabric", code="fabric", required=True
        )

        assert [attribute.code for attribute in tshirt.missing_attributes()] == ["fabric"]

    def test_an_attribute_answered_per_variant_is_never_missing_from_the_product(
        self, laptop: Product
    ) -> None:
        """Colour is required of the variants, so the product not answering it is right."""
        assert laptop.missing_attributes() == []


class TestProductQuerySet:
    def test_live_leaves_out_the_draft(self, laptop: Product, draft: Product) -> None:
        assert [product.slug for product in Product.objects.live()] == ["featherbook-14"]

    def test_a_branch_finds_what_is_in_the_categories_underneath(
        self, electronics: Category, laptop: Product, tshirt: Product
    ) -> None:
        found = Product.objects.live().in_branch(electronics)

        assert [product.slug for product in found] == ["featherbook-14"]

    def test_search_matches_the_brand_as_well_as_the_name(self, laptop: Product) -> None:
        assert [product.slug for product in Product.objects.search("acme")] == ["featherbook-14"]

    def test_search_narrows_with_every_word_rather_than_widening(self, laptop: Product) -> None:
        assert not Product.objects.search("acme unicycle").exists()

    def test_search_matches_a_tag(self, laptop: Product) -> None:
        assert [product.slug for product in Product.objects.search("portable")] == [
            "featherbook-14"
        ]

    def test_in_stock_leaves_out_a_product_whose_every_variant_is_empty(
        self, tshirt: Product
    ) -> None:
        tshirt.variants.update(stock=0)

        assert not Product.objects.live().in_stock().filter(pk=tshirt.pk).exists()


class TestVariant:
    def test_a_variant_has_to_answer_the_variant_attributes(self, tshirt: Product) -> None:
        variant = ProductVariant(product=tshirt, sku="TEE-X", options={})

        with pytest.raises(ValidationError):
            variant.save()

    def test_a_variant_cannot_answer_something_the_category_never_declared(
        self, tshirt: Product
    ) -> None:
        variant = ProductVariant(
            product=tshirt, sku="TEE-Y", options={"size": "S", "flavour": "Mint"}
        )

        with pytest.raises(ValidationError):
            variant.save()

    def test_two_variants_cannot_be_the_same_thing(self, tshirt: Product) -> None:
        duplicate = ProductVariant(product=tshirt, sku="TEE-M2", options={"size": "M"})

        with pytest.raises(ValidationError):
            duplicate.save()

    def test_a_variant_without_a_name_is_labelled_by_its_options(self, medium: Any) -> None:
        assert medium.label == "size: M"


class TestDiscount:
    def test_a_percentage_over_a_hundred_is_refused(self, db: None) -> None:
        with pytest.raises(ValidationError):
            Discount.objects.create(name="Free", kind=DiscountKind.PERCENT, value=Decimal("120"))

    def test_a_cap_means_nothing_on_a_fixed_amount_off(self, db: None) -> None:
        with pytest.raises(ValidationError):
            Discount.objects.create(
                name="Ten off",
                kind=DiscountKind.AMOUNT,
                value=Decimal("10"),
                max_amount=Decimal("5"),
            )

    def test_a_sale_cannot_end_before_it_starts(self, db: None) -> None:
        now = timezone.now()

        with pytest.raises(ValidationError):
            Discount.objects.create(
                name="Backwards",
                value=Decimal("10"),
                starts_at=now,
                ends_at=now - timezone.timedelta(hours=1),
            )

    def test_a_campaign_that_has_not_started_is_not_running(self, db: None) -> None:
        later = Discount.objects.create(
            name="Later",
            value=Decimal("10"),
            starts_at=timezone.now() + timezone.timedelta(days=1),
        )

        assert not later.is_running
        assert not Discount.objects.running().exists()

    def test_a_campaign_on_a_category_reaches_the_categories_underneath(
        self, electronics: Category, laptop: Product
    ) -> None:
        campaign = Discount.objects.create(name="Electronics week", value=Decimal("5"))
        campaign.categories.add(electronics)

        assert campaign.covers(laptop)

    def test_everything_in_the_shop_ignores_both_lists(
        self, laptop: Product, tshirt: Product
    ) -> None:
        campaign = Discount.objects.create(
            name="Everything", value=Decimal("5"), applies_to_all=True
        )

        assert campaign.covers(laptop)
        assert campaign.covers(tshirt)


class TestReviewStats:
    def test_a_published_review_moves_the_products_rating(
        self, laptop: Product, alice: Any
    ) -> None:
        Review.objects.create(
            product=laptop, user=alice, rating=4, body="Good.", status=ReviewStatus.APPROVED
        )
        laptop.refresh_from_db()

        assert laptop.rating_average == Decimal("4.00")
        assert laptop.rating_count == 1

    def test_a_review_waiting_for_a_moderator_counts_for_nothing(
        self, laptop: Product, alice: Any
    ) -> None:
        Review.objects.create(
            product=laptop, user=alice, rating=1, body="Hmm.", status=ReviewStatus.PENDING
        )
        laptop.refresh_from_db()

        assert laptop.rating_count == 0

    def test_deleting_a_review_puts_the_rating_back(self, laptop: Product, alice: Any) -> None:
        review = Review.objects.create(
            product=laptop, user=alice, rating=5, body="Great.", status=ReviewStatus.APPROVED
        )
        review.delete()
        laptop.refresh_from_db()

        assert laptop.rating_count == 0
        assert laptop.rating_average == Decimal("0.00")

    def test_a_rating_outside_one_to_five_is_refused(self, laptop: Product, alice: Any) -> None:
        with pytest.raises(ValidationError):
            Review.objects.create(product=laptop, user=alice, rating=6, body="Too much.")

    def test_recomputing_never_validates_the_whole_product(
        self, laptop: Product, alice: Any
    ) -> None:
        """A product that would fail validation still gets its rating updated.

        The counters go through `queryset.update`, deliberately: a review must
        not be blocked because somebody left the product in a state `clean`
        dislikes.
        """
        Product.objects.filter(pk=laptop.pk).update(compare_at_price=Decimal("1.00"))
        Review.objects.create(
            product=laptop, user=alice, rating=3, body="Fine.", status=ReviewStatus.APPROVED
        )
        refresh_review_stats(laptop.pk)
        laptop.refresh_from_db()

        assert laptop.rating_count == 1


class TestShippingMethod:
    def test_it_is_free_once_the_basket_is_big_enough(self, shipping: ShippingMethod) -> None:
        assert shipping.cost_for(Decimal("1000.00")) == Decimal("0")

    def test_it_costs_what_it_says_below_the_threshold(self, shipping: ShippingMethod) -> None:
        assert shipping.cost_for(Decimal("999.99")) == Decimal("5.00")


class TestProductStatusChoices:
    def test_an_archived_product_is_not_live_but_is_still_readable(self, laptop: Product) -> None:
        laptop.status = ProductStatus.ARCHIVED
        laptop.save()

        assert not laptop.is_live
        assert Product.objects.filter(pk=laptop.pk).exists()
