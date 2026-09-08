"""Several sellers of one thing, and which of them a shopper buys from.

The product row is the primary seller's offer -- its price, its stock. Every
other seller is a ``ProductOffer``. What has to hold across all of it is that
the price on the page, the price in the basket and the price on the order are
the same number, whoever it came from.
"""

from decimal import Decimal
from typing import Any

import pytest
from django.core.exceptions import ValidationError
from django.test import Client

from apps.shop.models import (
    Address,
    Discount,
    Product,
    ProductOffer,
    Seller,
    ShippingMethod,
)
from apps.shop.pricing import buy_box, price_of, sellable_offers
from apps.shop.services import ShopNotFound, ShopRefused, shop_service
from apps.shop.tests.conftest import bearer

pytestmark = pytest.mark.django_db

SHOP = "/api/v1/shop"


@pytest.fixture
def client() -> Client:
    return Client()


def data(response: Any) -> Any:
    assert response.status_code == 200, response.content
    return response.json()["data"]


class TestOfferValidation:
    def test_an_offer_on_a_variant_product_names_a_variant(
        self, tshirt: Product, resellers: Seller
    ) -> None:
        with pytest.raises(ValidationError):
            ProductOffer.objects.create(
                product=tshirt, seller=resellers, price=Decimal("18.00"), stock=1
            )

    def test_an_offer_on_a_plain_product_names_no_variant(
        self, laptop: Product, tshirt: Product, resellers: Seller
    ) -> None:
        with pytest.raises(ValidationError):
            ProductOffer.objects.create(
                product=laptop,
                seller=resellers,
                variant=tshirt.variants.first(),
                price=Decimal("1.00"),
            )

    def test_one_seller_cannot_offer_the_same_thing_twice(
        self, undercut: ProductOffer, laptop: Product, resellers: Seller
    ) -> None:
        with pytest.raises(ValidationError):
            ProductOffer.objects.create(
                product=laptop, seller=resellers, price=Decimal("999.00"), stock=1
            )

    def test_two_sellers_can_offer_the_same_thing(
        self, undercut: ProductOffer, laptop: Product, acme_store: Seller
    ) -> None:
        second = ProductOffer.objects.create(
            product=laptop, seller=acme_store, price=Decimal("1150.00"), stock=1
        )

        assert laptop.offers.count() == 2
        assert second.pk


class TestTheBuyBox:
    def test_with_no_offers_the_product_sells_itself(self, laptop: Product) -> None:
        offer, price = buy_box(laptop)

        assert offer is None
        assert price.amount == Decimal("1200.00")

    def test_a_cheaper_seller_wins_the_page(self, laptop: Product, undercut: ProductOffer) -> None:
        offer, price = buy_box(laptop)

        assert offer == undercut
        assert price.amount == Decimal("1100.00")

    def test_a_dearer_seller_does_not(self, laptop: Product, resellers: Seller) -> None:
        ProductOffer.objects.create(
            product=laptop, seller=resellers, price=Decimal("1300.00"), stock=5
        )

        assert buy_box(laptop)[0] is None

    def test_a_hidden_offer_is_not_a_candidate(
        self, laptop: Product, undercut: ProductOffer
    ) -> None:
        undercut.is_active = False
        undercut.save()

        assert buy_box(laptop)[0] is None

    def test_a_hidden_sellers_offer_is_not_a_candidate(
        self, laptop: Product, undercut: ProductOffer, resellers: Seller
    ) -> None:
        """Turning a seller off has to take their whole shelf with it."""
        resellers.is_active = False
        resellers.save()
        laptop.refresh_from_db()

        assert buy_box(laptop)[0] is None

    def test_an_empty_offer_is_not_a_candidate(
        self, laptop: Product, undercut: ProductOffer
    ) -> None:
        undercut.stock = 0
        undercut.save()

        assert buy_box(laptop)[0] is None

    def test_a_seller_wins_when_the_shop_itself_has_run_out(
        self, laptop: Product, undercut: ProductOffer
    ) -> None:
        """The whole point of a second seller."""
        Product.objects.filter(pk=laptop.pk).update(stock=0)
        laptop.refresh_from_db()

        assert buy_box(laptop)[0] == undercut

    def test_a_campaign_reaches_a_sellers_price_too(
        self, laptop: Product, undercut: ProductOffer, sale: Discount
    ) -> None:
        """Otherwise the cheapest listing becomes the dearest when a sale starts."""
        offer, price = buy_box(laptop)

        assert offer == undercut
        assert price.amount == Decimal("990.00")
        assert price.discount is not None

    def test_the_winner_is_decided_after_the_campaign_rather_than_before(
        self, laptop: Product, resellers: Seller
    ) -> None:
        """A campaign takes the same percentage off both, so the order is kept --
        and what a shopper is quoted is the discounted number, not the raw one."""
        offer = ProductOffer.objects.create(
            product=laptop, seller=resellers, price=Decimal("1100.00"), stock=1
        )
        campaign = Discount.objects.create(name="Half off", value=Decimal("50"))
        campaign.products.add(laptop)

        chosen, price = buy_box(laptop)

        assert chosen == offer
        assert price.amount == Decimal("550.00")

    def test_a_tie_goes_to_whoever_dispatches_sooner(
        self, laptop: Product, acme_store: Seller, resellers: Seller
    ) -> None:
        slow = ProductOffer.objects.create(
            product=laptop, seller=resellers, price=Decimal("1000.00"), stock=1, lead_time_days=9
        )
        quick = ProductOffer.objects.create(
            product=laptop, seller=acme_store, price=Decimal("1000.00"), stock=1, lead_time_days=1
        )

        assert buy_box(laptop)[0] == quick
        assert slow.pk

    def test_nothing_buyable_still_quotes_a_price(self, laptop: Product) -> None:
        """A page saying "out of stock" needs a number under it."""
        Product.objects.filter(pk=laptop.pk).update(stock=0)
        laptop.refresh_from_db()

        offer, price = buy_box(laptop)

        assert offer is None
        assert price.amount == Decimal("1200.00")

    def test_offers_are_listed_cheapest_first(
        self, laptop: Product, acme_store: Seller, resellers: Seller
    ) -> None:
        ProductOffer.objects.create(
            product=laptop, seller=resellers, price=Decimal("1150.00"), stock=1
        )
        ProductOffer.objects.create(
            product=laptop, seller=acme_store, price=Decimal("1050.00"), stock=1
        )

        assert [offer.price for offer in sellable_offers(laptop)] == [
            Decimal("1050.00"),
            Decimal("1150.00"),
        ]

    def test_a_variants_offers_are_kept_apart(self, tshirt: Product, resellers: Seller) -> None:
        medium = tshirt.variants.get(sku="TEE-M")
        large = tshirt.variants.get(sku="TEE-L")
        ProductOffer.objects.create(
            product=tshirt,
            seller=resellers,
            variant=large,
            price=Decimal("15.00"),
            stock=5,
        )

        assert sellable_offers(tshirt, medium) == []
        assert len(sellable_offers(tshirt, large)) == 1


class TestStockCountsEverySeller:
    def test_a_product_is_in_stock_when_only_a_seller_has_it(
        self, laptop: Product, undercut: ProductOffer
    ) -> None:
        Product.objects.filter(pk=laptop.pk).update(stock=0)
        laptop.refresh_from_db()

        assert laptop.in_stock

    def test_the_in_stock_listing_finds_it_too(
        self, laptop: Product, undercut: ProductOffer
    ) -> None:
        Product.objects.filter(pk=laptop.pk).update(stock=0)

        found = Product.objects.live().in_stock()

        assert [product.slug for product in found] == ["featherbook-14"]

    def test_a_product_nobody_stocks_is_out_of_stock(
        self, laptop: Product, undercut: ProductOffer
    ) -> None:
        Product.objects.filter(pk=laptop.pk).update(stock=0)
        undercut.stock = 0
        undercut.save()
        laptop.refresh_from_db()

        assert not laptop.in_stock


class TestTheApi:
    def test_the_sellers_are_listed(
        self, client: Client, acme_store: Seller, resellers: Seller
    ) -> None:
        rows = data(client.get(f"{SHOP}/sellers"))

        assert [row["id"] for row in rows] == ["acme-store", "bargain-bin"]

    def test_a_hidden_seller_is_not_listed(self, client: Client, acme_store: Seller) -> None:
        acme_store.is_active = False
        acme_store.save()

        assert data(client.get(f"{SHOP}/sellers")) == []

    def test_one_seller_says_how_much_they_carry(
        self, client: Client, laptop: Product, undercut: ProductOffer, resellers: Seller
    ) -> None:
        seller = data(client.get(f"{SHOP}/sellers/bargain-bin"))

        assert seller["name"] == "Bargain Bin"
        assert seller["product_count"] == 1

    def test_an_unknown_seller_is_a_404(self, client: Client, db: None) -> None:
        assert client.get(f"{SHOP}/sellers/nonesuch").status_code == 404

    def test_a_product_page_names_who_sells_it(
        self, client: Client, laptop: Product, acme_store: Seller
    ) -> None:
        laptop.seller = acme_store
        laptop.save()

        product = data(client.get(f"{SHOP}/products/featherbook-14"))

        assert product["seller"] == "Acme Store"
        assert product["sold_by"]["slug"] == "acme-store"

    def test_a_product_page_lists_the_other_sellers(
        self, client: Client, laptop: Product, undercut: ProductOffer
    ) -> None:
        product = data(client.get(f"{SHOP}/products/featherbook-14"))

        [offer] = product["offers"]
        assert offer["seller"]["name"] == "Bargain Bin"
        assert offer["price"]["amount"] == "1100.00"
        assert offer["lead_time_days"] == 2

    def test_the_headline_price_is_the_cheapest_seller(
        self, client: Client, laptop: Product, undercut: ProductOffer
    ) -> None:
        product = data(client.get(f"{SHOP}/products/featherbook-14"))

        assert product["price"]["amount"] == "1100.00"

    def test_a_listing_prices_at_the_buy_box_too(
        self, client: Client, laptop: Product, undercut: ProductOffer
    ) -> None:
        """A card that quoted the product's own price would disagree with the page."""
        page = data(client.get(f"{SHOP}/products"))

        assert page["items"][0]["price"]["amount"] == "1100.00"

    def test_a_seller_filter_finds_what_they_sell_directly(
        self, client: Client, laptop: Product, tshirt: Product, acme_store: Seller
    ) -> None:
        laptop.seller = acme_store
        laptop.save()

        page = data(client.get(f"{SHOP}/products?seller=acme-store"))

        assert [row["id"] for row in page["items"]] == ["featherbook-14"]

    def test_a_seller_filter_finds_what_they_offer_on_somebody_elses_listing(
        self, client: Client, laptop: Product, tshirt: Product, undercut: ProductOffer
    ) -> None:
        page = data(client.get(f"{SHOP}/products?seller=bargain-bin"))

        assert [row["id"] for row in page["items"]] == ["featherbook-14"]

    def test_a_seller_filter_for_nobody_returns_nothing(
        self, client: Client, laptop: Product
    ) -> None:
        assert data(client.get(f"{SHOP}/products?seller=nobody"))["items"] == []


class TestBuyingFromASeller:
    def _add(self, client: Client, user: Any, **payload: Any) -> Any:
        payload.setdefault("product", "featherbook-14")
        return client.post(
            f"{SHOP}/cart/items", data=payload, content_type="application/json", **bearer(user)
        )

    def test_a_basket_takes_the_seller_the_page_was_showing(
        self, client: Client, laptop: Product, undercut: ProductOffer, alice: Any
    ) -> None:
        cart = data(self._add(client, alice))

        [line] = cart["items"]
        assert line["seller"] == "Bargain Bin"
        assert line["unit_price"]["amount"] == "1100.00"

    def test_a_shopper_can_name_a_dearer_seller(
        self,
        client: Client,
        laptop: Product,
        undercut: ProductOffer,
        acme_store: Seller,
        alice: Any,
    ) -> None:
        dearer = ProductOffer.objects.create(
            product=laptop, seller=acme_store, price=Decimal("1250.00"), stock=1
        )

        cart = data(self._add(client, alice, offer=str(dearer.pk)))

        assert cart["items"][0]["unit_price"]["amount"] == "1250.00"

    def test_two_sellers_of_one_thing_are_two_lines(
        self,
        client: Client,
        laptop: Product,
        undercut: ProductOffer,
        acme_store: Seller,
        alice: Any,
    ) -> None:
        other = ProductOffer.objects.create(
            product=laptop, seller=acme_store, price=Decimal("1250.00"), stock=1
        )
        self._add(client, alice, offer=str(undercut.pk))

        cart = data(self._add(client, alice, offer=str(other.pk)))

        assert cart["item_count"] == 2

    def test_adding_the_same_sellers_line_twice_adds_up(
        self, client: Client, laptop: Product, undercut: ProductOffer, alice: Any
    ) -> None:
        self._add(client, alice, offer=str(undercut.pk))
        cart = data(self._add(client, alice, offer=str(undercut.pk)))

        assert cart["item_count"] == 1
        assert cart["items"][0]["quantity"] == 2

    def test_an_offer_of_another_product_is_a_404(
        self, client: Client, laptop: Product, tshirt: Product, resellers: Seller, alice: Any
    ) -> None:
        theirs = ProductOffer.objects.create(
            product=tshirt,
            seller=resellers,
            variant=tshirt.variants.get(sku="TEE-M"),
            price=Decimal("15.00"),
            stock=1,
        )

        assert self._add(client, alice, offer=str(theirs.pk)).status_code == 404

    def test_a_hidden_offer_cannot_be_bought_from(
        self, client: Client, laptop: Product, undercut: ProductOffer, alice: Any
    ) -> None:
        undercut.is_active = False
        undercut.save()

        assert self._add(client, alice, offer=str(undercut.pk)).status_code == 404

    def test_more_than_that_seller_has_is_refused(
        self, client: Client, laptop: Product, undercut: ProductOffer, alice: Any
    ) -> None:
        """Two on their shelf, five on the shop's: asking for four must fail."""
        response = self._add(client, alice, offer=str(undercut.pk), quantity=4)

        assert response.status_code == 400
        assert "Only 2 left" in response.json()["errors"][0]

    def test_a_nonsense_offer_id_is_a_404_rather_than_a_500(
        self, client: Client, laptop: Product, alice: Any
    ) -> None:
        assert self._add(client, alice, offer="banana").status_code == 404


class TestOrderingFromASeller:
    def test_the_order_line_records_who_sold_it(
        self,
        laptop: Product,
        undercut: ProductOffer,
        alice: Any,
        address: Address,
        shipping: ShippingMethod,
    ) -> None:
        shop_service.add_to_cart(alice, "featherbook-14")
        order = shop_service.checkout(alice, address_id=address.pk, shipping_method_id=shipping.pk)

        line = order.items.get()
        assert line.seller == undercut.seller
        assert line.seller_name == "Bargain Bin"
        assert line.unit_price == Decimal("1100.00")

    def test_the_sellers_stock_is_the_stock_that_moves(
        self,
        laptop: Product,
        undercut: ProductOffer,
        alice: Any,
        address: Address,
        shipping: ShippingMethod,
    ) -> None:
        shop_service.add_to_cart(alice, "featherbook-14", quantity=2)
        shop_service.checkout(alice, address_id=address.pk, shipping_method_id=shipping.pk)

        undercut.refresh_from_db()
        laptop.refresh_from_db()
        assert undercut.stock == 0
        assert laptop.stock == 5

    def test_cancelling_puts_it_back_on_the_sellers_shelf(
        self,
        laptop: Product,
        undercut: ProductOffer,
        alice: Any,
        address: Address,
        shipping: ShippingMethod,
    ) -> None:
        shop_service.add_to_cart(alice, "featherbook-14", quantity=2)
        order = shop_service.checkout(alice, address_id=address.pk, shipping_method_id=shipping.pk)

        shop_service.cancel_order(alice, order.number)

        undercut.refresh_from_db()
        laptop.refresh_from_db()
        assert undercut.stock == 2
        assert laptop.stock == 5

    def test_the_sellers_own_code_is_what_the_line_quotes(
        self,
        laptop: Product,
        undercut: ProductOffer,
        alice: Any,
        address: Address,
        shipping: ShippingMethod,
    ) -> None:
        undercut.sku = "BB-9911"
        undercut.save()
        shop_service.add_to_cart(alice, "featherbook-14")

        order = shop_service.checkout(alice, address_id=address.pk, shipping_method_id=shipping.pk)

        assert order.items.get().sku == "BB-9911"

    def test_a_line_stops_being_fillable_when_the_seller_sells_out(
        self, laptop: Product, undercut: ProductOffer, alice: Any
    ) -> None:
        shop_service.add_to_cart(alice, "featherbook-14", quantity=2)
        ProductOffer.objects.filter(pk=undercut.pk).update(stock=0)

        cart = shop_service.cart(alice)

        assert cart["items"][0]["in_stock"] is False

    def test_a_basket_the_seller_can_no_longer_fill_is_refused_at_checkout(
        self,
        laptop: Product,
        undercut: ProductOffer,
        alice: Any,
        address: Address,
        shipping: ShippingMethod,
    ) -> None:
        shop_service.add_to_cart(alice, "featherbook-14", quantity=2)
        ProductOffer.objects.filter(pk=undercut.pk).update(stock=1)

        with pytest.raises(ShopRefused):
            shop_service.checkout(alice, address_id=address.pk, shipping_method_id=shipping.pk)

    def test_a_seller_cannot_be_deleted_while_a_product_names_them(
        self, laptop: Product, acme_store: Seller
    ) -> None:
        """Protected, because an order's line points at them."""
        from django.db.models import ProtectedError

        laptop.seller = acme_store
        laptop.save()

        with pytest.raises(ProtectedError):
            acme_store.delete()


class TestPricingHelpers:
    def test_an_offer_beats_a_variant_override(self, tshirt: Product, resellers: Seller) -> None:
        large = tshirt.variants.get(sku="TEE-L")
        offer = ProductOffer.objects.create(
            product=tshirt, seller=resellers, variant=large, price=Decimal("16.00"), stock=1
        )

        assert price_of(tshirt, large, offer=offer).amount == Decimal("16.00")

    def test_an_unknown_seller_read_is_a_lookup_failure(self, db: None) -> None:
        with pytest.raises(ShopNotFound):
            shop_service.seller("nonesuch")
