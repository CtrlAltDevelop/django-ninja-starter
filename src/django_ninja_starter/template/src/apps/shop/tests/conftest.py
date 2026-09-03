"""Fixtures for the shop tests: a catalogue somebody could actually buy from.

The app is optional, so its tests are too. A project that has not enabled it in
``DJANGO_SHOP_ENABLED`` has no shop tables and no registered models, and
importing one raises before pytest can say anything useful -- so collection
stops here instead, and the rest of that project's suite runs as normal.

The catalogue below is deliberately not minimal. Half of what this app does only
shows up when there is a category tree rather than a category, a product with
variants beside one without, and something out of stock next to something on
sale -- so every one of those exists here once and is shared by every test.
"""

from collections.abc import Iterator
from decimal import Decimal
from typing import Any

import pytest
from django.apps import apps as django_apps
from django.contrib.auth import get_user_model
from django.test import RequestFactory
from django.utils import timezone

SHOP_INSTALLED = django_apps.is_installed("apps.shop")
collect_ignore_glob = [] if SHOP_INSTALLED else ["*"]

if SHOP_INSTALLED:
    from apps.shop.attributes import AttributeType
    from apps.shop.models import (
        Address,
        Brand,
        Category,
        CategoryAttribute,
        Collection,
        CollectionItem,
        Coupon,
        Discount,
        DiscountKind,
        Product,
        ProductAttribute,
        ProductImage,
        ProductOffer,
        ProductStatus,
        ProductVariant,
        Seller,
        ShippingMethod,
        Tag,
    )


def access_token(user: Any) -> str:
    """A real credential from the project's own issuer, not a hand-rolled JWT.

    Minting it the way a login does is the point: a token one transport accepts
    and another would not is exactly the bug worth catching, and all three of
    this app's doors read the same one.
    """
    from infrastructure.auth.core.sessions import issue_credentials

    request = RequestFactory().post("/")
    return issue_credentials(request, user, method="password").access_token


def bearer(user: Any) -> dict[str, str]:
    """The header kwargs Django's test client wants for a signed-in request."""
    return {"HTTP_AUTHORIZATION": f"Bearer {access_token(user)}"}


@pytest.fixture(autouse=True)
def _published_reviews() -> Iterator[None]:
    """Most tests care what a review says, not about moderating it.

    Moderation is on by default and has its own tests, which turn it back on
    explicitly. Leaving it on here would mean every other test writing a review
    and then reaching into the table to approve it.
    """
    from django.test import override_settings

    with override_settings(SHOP_REVIEW_MODERATION=False):
        yield


@pytest.fixture
def alice(db: None) -> Any:
    return get_user_model().objects.create_user(username="alice", email="alice@example.test")


@pytest.fixture
def bob(db: None) -> Any:
    return get_user_model().objects.create_user(username="bob", email="bob@example.test")


@pytest.fixture
def electronics(db: None) -> Category:
    """A root category. Its attributes are inherited by everything beneath it."""
    category = Category.objects.create(name="Electronics", slug="electronics", order=0)
    CategoryAttribute.objects.create(
        category=category,
        name="Warranty",
        code="warranty",
        attribute_type=AttributeType.NUMBER,
        unit="months",
        order=0,
    )
    return category


@pytest.fixture
def laptops(electronics: Category) -> Category:
    """A child category, with an attribute of its own and one that varies per variant."""
    category = Category.objects.create(name="Laptops", slug="laptops", parent=electronics, order=0)
    CategoryAttribute.objects.create(
        category=category,
        name="Screen size",
        code="screen-size",
        attribute_type=AttributeType.NUMBER,
        unit="in",
        required=True,
        order=1,
    )
    CategoryAttribute.objects.create(
        category=category,
        name="Colour",
        code="colour",
        attribute_type=AttributeType.CHOICE,
        choices=["Silver", "Space grey"],
        is_variant=True,
        order=2,
    )
    return category


@pytest.fixture
def shirts(db: None) -> Category:
    """A second root, so "everything under electronics" is a real question."""
    return Category.objects.create(name="Shirts", slug="shirts", order=1)


@pytest.fixture
def acme(db: None) -> Brand:
    return Brand.objects.create(name="Acme", slug="acme", website="https://acme.example.test")


@pytest.fixture
def laptop(laptops: Category, acme: Brand) -> Product:
    """The ordinary case: live, in stock, no variants, one picture, one spec."""
    product = Product.objects.create(
        category=laptops,
        brand=acme,
        name="Acme Featherbook 14",
        slug="featherbook-14",
        subtitle="Thin, light, quiet",
        summary="A small laptop for writing on trains.",
        description="Fourteen inches of quiet aluminium.",
        sku="FB-14",
        price=Decimal("1200.00"),
        status=ProductStatus.ACTIVE,
        stock=5,
        is_featured=True,
        sales_count=40,
    )
    product.tags.add(Tag.objects.create(name="Portable", slug="portable"))
    ProductImage.objects.create(
        product=product, url="https://cdn.example.test/fb14.jpg", alt="A laptop", is_primary=True
    )
    ProductAttribute.objects.create(
        product=product,
        attribute=laptops.attributes.get(code="screen-size"),
        value=14,
    )
    return product


@pytest.fixture
def tshirt(shirts: Category) -> Product:
    """The variant case: nothing buyable except through one of its variants."""
    CategoryAttribute.objects.create(
        category=shirts,
        name="Size",
        code="size",
        attribute_type=AttributeType.CHOICE,
        choices=["S", "M", "L"],
        is_variant=True,
    )
    product = Product.objects.create(
        category=shirts,
        name="Plain tee",
        slug="plain-tee",
        summary="A shirt with nothing on it.",
        sku="TEE",
        price=Decimal("20.00"),
        status=ProductStatus.ACTIVE,
        has_variants=True,
        like_count=0,
    )
    ProductVariant.objects.create(
        product=product, sku="TEE-M", options={"size": "M"}, stock=3, order=0
    )
    ProductVariant.objects.create(
        product=product,
        sku="TEE-L",
        options={"size": "L"},
        price=Decimal("22.00"),
        stock=0,
        order=1,
    )
    return product


@pytest.fixture
def draft(laptops: Category) -> Product:
    """Never published. Nothing public may mention it."""
    return Product.objects.create(
        category=laptops,
        name="Unannounced thing",
        slug="unannounced",
        sku="SECRET",
        price=Decimal("99.00"),
        status=ProductStatus.DRAFT,
        stock=1,
    )


@pytest.fixture
def medium(tshirt: Product) -> ProductVariant:
    return tshirt.variants.get(sku="TEE-M")


@pytest.fixture
def sale(laptop: Product) -> Discount:
    """Ten percent off the one laptop, running right now."""
    discount = Discount.objects.create(
        name="Spring sale",
        kind=DiscountKind.PERCENT,
        value=Decimal("10.00"),
        starts_at=timezone.now() - timezone.timedelta(days=1),
        ends_at=timezone.now() + timezone.timedelta(days=1),
    )
    discount.products.add(laptop)
    return discount


@pytest.fixture
def staff_picks(laptop: Product) -> Collection:
    collection = Collection.objects.create(
        name="Staff picks", slug="staff-picks", description="What we would buy."
    )
    CollectionItem.objects.create(collection=collection, product=laptop, order=0)
    return collection


@pytest.fixture
def address(alice: Any) -> Address:
    return Address.objects.create(
        user=alice,
        full_name="Alice Example",
        phone="+441234567890",
        country="GB",
        city="Bristol",
        postal_code="BS1 4ST",
        line1="1 Example Street",
    )


@pytest.fixture
def shipping(db: None) -> ShippingMethod:
    """Priced, with a threshold above which it is free -- both branches of `cost_for`."""
    return ShippingMethod.objects.create(
        name="Standard", price=Decimal("5.00"), free_from=Decimal("1000.00")
    )


@pytest.fixture
def coupon(db: None) -> Coupon:
    return Coupon.objects.create(code="WELCOME", percent=Decimal("5.00"))


@pytest.fixture
def acme_store(db: None) -> Seller:
    """The shop's own primary seller, named on the laptop."""
    return Seller.objects.create(name="Acme Store", slug="acme-store", city="Bristol", order=0)


@pytest.fixture
def resellers(db: None) -> Seller:
    """A second seller, so "who else sells this" is a real question."""
    return Seller.objects.create(name="Bargain Bin", slug="bargain-bin", city="Leeds", order=1)


@pytest.fixture
def undercut(laptop: Product, resellers: Seller) -> ProductOffer:
    """Another seller, cheaper than the shop, with stock."""
    return ProductOffer.objects.create(
        product=laptop, seller=resellers, price=Decimal("1100.00"), stock=2, lead_time_days=2
    )
