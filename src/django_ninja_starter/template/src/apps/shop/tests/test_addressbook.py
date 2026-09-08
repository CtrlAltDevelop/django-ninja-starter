"""The three things a checkout page needs before it can call checkout.

Checkout wants an ``address_id`` and a ``shipping_method_id``, and a shopper
who has never bought anything has neither. So: somewhere to save an address,
somewhere to read the delivery options with their cost against *this* basket,
and somewhere to try a coupon code before committing to it. All three exist on
all three doors, and the tests below check the same answers arrive from each.
"""

import json
from collections.abc import Callable
from decimal import Decimal
from typing import Any

import grpc
import pytest
from django.test import Client
from google.protobuf.empty_pb2 import Empty

from apps.shop.grpc import shop_pb2, shop_pb2_grpc
from apps.shop.models import Address, Coupon, Product, ShippingMethod
from apps.shop.services import ShopNotFound, ShopRefused, shop_service
from apps.shop.tests.conftest import access_token, bearer

pytestmark = pytest.mark.django_db

SHOP = "/api/v1/shop"
Stub = shop_pb2_grpc.ShopControllerStub

NEW = {
    "full_name": "Alice Example",
    "phone": "+441234567890",
    "country": "GB",
    "city": "Bath",
    "postal_code": "BA1 1AA",
    "line1": "2 Example Street",
}

GRAPH_NEW = {
    "fullName": NEW["full_name"],
    "phone": NEW["phone"],
    "country": NEW["country"],
    "city": NEW["city"],
    "postalCode": NEW["postal_code"],
    "line1": NEW["line1"],
}


def graphql(query: str, user: Any = None, **variables: Any) -> dict[str, Any]:
    headers = {"HTTP_AUTHORIZATION": f"Bearer {access_token(user)}"} if user else {}
    response = Client().post(
        "/graphql",
        data={"query": query, "variables": variables},
        content_type="application/json",
        **headers,
    )
    assert response.status_code == 200, response.content
    return json.loads(response.content)


class TestTheAddressBook:
    def test_the_first_address_an_account_saves_becomes_its_default(self, alice: Any) -> None:
        saved = shop_service.add_address(alice, **NEW)

        assert saved["is_default"]

    def test_a_later_one_does_not_take_the_default_unasked(
        self, alice: Any, address: Address
    ) -> None:
        saved = shop_service.add_address(alice, **NEW)

        assert not saved["is_default"]
        assert Address.objects.get(pk=address.pk).is_default

    def test_choosing_a_default_unchooses_the_last(self, alice: Any, address: Address) -> None:
        second = shop_service.add_address(alice, **NEW)

        shop_service.set_default_address(alice, second["id"])

        assert not Address.objects.get(pk=address.pk).is_default
        assert Address.objects.get(pk=second["id"]).is_default

    def test_the_default_is_read_back_first(self, alice: Any, address: Address) -> None:
        second = shop_service.add_address(alice, **NEW)
        shop_service.set_default_address(alice, second["id"])

        assert [row["id"] for row in shop_service.addresses(alice)][0] == second["id"]

    def test_an_edit_leaves_out_what_the_caller_did_not_send(
        self, alice: Any, address: Address
    ) -> None:
        edited = shop_service.update_address(alice, address.pk, city="Bath")

        assert edited["city"] == "Bath"
        assert edited["line1"] == "1 Example Street"

    def test_editing_an_address_never_rewrites_an_order_sent_to_it(
        self, alice: Any, address: Address, shipping: ShippingMethod, laptop: Product
    ) -> None:
        shop_service.add_to_cart(alice, "featherbook-14", quantity=1)
        order = shop_service.checkout(alice, address_id=address.pk, shipping_method_id=shipping.pk)

        shop_service.update_address(alice, address.pk, city="Bath")

        order.refresh_from_db()
        assert order.shipping_address["city"] == "Bristol"

    def test_removing_the_default_hands_it_to_another(self, alice: Any, address: Address) -> None:
        second = shop_service.add_address(alice, **NEW)

        shop_service.remove_address(alice, address.pk)

        assert Address.objects.get(pk=second["id"]).is_default

    def test_somebody_else_s_address_does_not_exist(
        self, alice: Any, bob: Any, address: Address
    ) -> None:
        with pytest.raises(ShopNotFound):
            shop_service.address(bob, address.pk)

    def test_nor_can_somebody_else_delete_it(self, bob: Any, address: Address) -> None:
        with pytest.raises(ShopNotFound):
            shop_service.remove_address(bob, address.pk)


class TestTheDeliveryOptions:
    def test_a_basket_below_the_threshold_pays_for_delivery(
        self, alice: Any, shipping: ShippingMethod, medium: Any
    ) -> None:
        shop_service.add_to_cart(alice, "plain-tee", quantity=1, variant_id=medium.pk)

        [method] = shop_service.shipping_methods(alice)

        assert method["cost"] == Decimal("5.00")
        assert not method["is_free"]

    def test_a_basket_above_it_does_not(
        self, alice: Any, shipping: ShippingMethod, laptop: Product
    ) -> None:
        shop_service.add_to_cart(alice, "featherbook-14", quantity=1)

        [method] = shop_service.shipping_methods(alice)

        assert method["cost"] == Decimal("0.00")
        assert method["is_free"]

    def test_asked_without_an_account_it_quotes_the_list_price(
        self, shipping: ShippingMethod
    ) -> None:
        [method] = shop_service.shipping_methods()

        assert method["cost"] == method["price"] == Decimal("5.00")
        assert not method["is_free"]

    def test_a_switched_off_method_is_not_offered(self, shipping: ShippingMethod) -> None:
        ShippingMethod.objects.filter(pk=shipping.pk).update(is_active=False)

        assert shop_service.shipping_methods() == []


class TestTryingACoupon:
    def test_a_good_code_says_what_it_is_worth(
        self, alice: Any, coupon: Coupon, laptop: Product
    ) -> None:
        shop_service.add_to_cart(alice, "featherbook-14", quantity=1)

        preview = shop_service.preview_coupon(alice, "welcome")

        assert preview["is_valid"]
        assert preview["discount"] == Decimal("60.00")
        assert preview["total"] == Decimal("1140.00")

    def test_an_unknown_code_is_an_answer_rather_than_an_error(self, alice: Any) -> None:
        preview = shop_service.preview_coupon(alice, "nonesuch")

        assert not preview["is_valid"]
        assert preview["reason"] == "No such code."
        assert preview["discount"] == Decimal("0.00")

    def test_a_code_below_its_minimum_says_so_without_refusing(
        self, alice: Any, medium: Any
    ) -> None:
        Coupon.objects.create(
            code="BIGSPEND", percent=Decimal("10.00"), minimum_subtotal=Decimal("500.00")
        )
        shop_service.add_to_cart(alice, "plain-tee", quantity=1, variant_id=medium.pk)

        preview = shop_service.preview_coupon(alice, "BIGSPEND")

        assert not preview["is_valid"]
        assert preview["reason"]
        assert preview["discount"] == Decimal("0.00")

    def test_an_empty_code_is_a_refusal(self, alice: Any) -> None:
        with pytest.raises(ShopRefused):
            shop_service.preview_coupon(alice, "   ")


class TestOverTheRoutes:
    def test_the_address_book_is_the_caller_s_own(self, alice: Any, address: Address) -> None:
        body = Client().get(f"{SHOP}/addresses", **bearer(alice)).json()

        assert [row["id"] for row in body["data"]] == [str(address.pk)]

    def test_it_needs_a_credential(self, address: Address) -> None:
        assert Client().get(f"{SHOP}/addresses").status_code == 401

    def test_an_address_is_saved_read_back_and_forgotten(self, alice: Any) -> None:
        client = Client()
        saved = client.post(
            f"{SHOP}/addresses", NEW, content_type="application/json", **bearer(alice)
        ).json()["data"]

        read = client.get(f"{SHOP}/addresses/{saved['id']}", **bearer(alice)).json()["data"]
        assert read["city"] == "Bath"

        removed = client.delete(f"{SHOP}/addresses/{saved['id']}", **bearer(alice)).json()
        assert removed["data"]["deleted"]
        assert not Address.objects.filter(pk=saved["id"]).exists()

    def test_an_edit_goes_over_patch(self, alice: Any, address: Address) -> None:
        body = (
            Client()
            .patch(
                f"{SHOP}/addresses/{address.pk}",
                {"city": "Bath"},
                content_type="application/json",
                **bearer(alice),
            )
            .json()
        )

        assert body["data"]["city"] == "Bath"
        assert body["data"]["line1"] == "1 Example Street"

    def test_somebody_else_s_address_is_a_404(self, bob: Any, address: Address) -> None:
        assert Client().get(f"{SHOP}/addresses/{address.pk}", **bearer(bob)).status_code == 404

    def test_the_delivery_options_are_public(self, shipping: ShippingMethod) -> None:
        body = Client().get(f"{SHOP}/shipping-methods").json()

        assert [row["name"] for row in body["data"]] == ["Standard"]

    def test_they_are_costed_for_the_caller_s_basket(
        self, alice: Any, shipping: ShippingMethod, laptop: Product
    ) -> None:
        shop_service.add_to_cart(alice, "featherbook-14", quantity=1)

        body = Client().get(f"{SHOP}/shipping-methods", **bearer(alice)).json()

        assert Decimal(body["data"][0]["cost"]) == 0

    def test_a_coupon_is_tried_without_being_used(
        self, alice: Any, coupon: Coupon, laptop: Product
    ) -> None:
        shop_service.add_to_cart(alice, "featherbook-14", quantity=1)

        body = (
            Client()
            .post(
                f"{SHOP}/cart/coupon",
                {"code": "WELCOME"},
                content_type="application/json",
                **bearer(alice),
            )
            .json()
        )

        assert body["data"]["is_valid"]
        assert Decimal(body["data"]["discount"]) == Decimal("60.00")

    def test_the_whole_checkout_is_reachable_from_an_empty_account(
        self, alice: Any, shipping: ShippingMethod, laptop: Product
    ) -> None:
        """The point of all of the above: no fixture, only routes."""
        client = Client()
        auth = bearer(alice)
        client.post(
            f"{SHOP}/cart/items",
            {"product": "featherbook-14", "quantity": 1},
            content_type="application/json",
            **auth,
        )
        address_id = client.post(
            f"{SHOP}/addresses", NEW, content_type="application/json", **auth
        ).json()["data"]["id"]
        method_id = client.get(f"{SHOP}/shipping-methods", **auth).json()["data"][0]["id"]

        placed = client.post(
            f"{SHOP}/checkout",
            {"address": address_id, "shipping_method": method_id},
            content_type="application/json",
            **auth,
        )

        assert placed.status_code == 200, placed.content
        assert placed.json()["data"]["status"] == "pending"


ADDRESSES = "{ shopAddresses { id city isDefault } }"
ADD_ADDRESS = """
mutation($address: AddressInput!) {
  shopAddAddress(address: $address) { id city isDefault }
}
"""
SET_DEFAULT = """
mutation($addressId: String!) {
  shopSetDefaultAddress(addressId: $addressId) { id isDefault }
}
"""
REMOVE_ADDRESS = """
mutation($addressId: String!) { shopRemoveAddress(addressId: $addressId) { deleted } }
"""
SHIPPING_METHODS = "{ shopShippingMethods { id name cost isFree } }"
COUPON_PREVIEW = """
query($code: String!) { shopCouponPreview(code: $code) { code isValid reason discount } }
"""


class TestOverGraphql:
    def test_the_address_book_reads_back(self, alice: Any, address: Address) -> None:
        rows = graphql(ADDRESSES, alice)["data"]["shopAddresses"]

        assert [row["city"] for row in rows] == ["Bristol"]

    def test_an_address_is_saved(self, alice: Any) -> None:
        saved = graphql(ADD_ADDRESS, alice, address=GRAPH_NEW)["data"]["shopAddAddress"]

        assert saved["city"] == "Bath"
        assert saved["isDefault"]

    def test_the_default_is_chosen_and_the_address_forgotten(
        self, alice: Any, address: Address
    ) -> None:
        second = graphql(ADD_ADDRESS, alice, address=GRAPH_NEW)["data"]["shopAddAddress"]

        chosen = graphql(SET_DEFAULT, alice, addressId=second["id"])["data"][
            "shopSetDefaultAddress"
        ]
        assert chosen["isDefault"]

        gone = graphql(REMOVE_ADDRESS, alice, addressId=second["id"])["data"]["shopRemoveAddress"]
        assert gone["deleted"]

    def test_the_delivery_options_are_costed_for_the_basket(
        self, alice: Any, shipping: ShippingMethod, laptop: Product
    ) -> None:
        shop_service.add_to_cart(alice, "featherbook-14", quantity=1)

        [method] = graphql(SHIPPING_METHODS, alice)["data"]["shopShippingMethods"]

        assert method["isFree"]

    def test_a_coupon_is_previewed(self, alice: Any, coupon: Coupon, laptop: Product) -> None:
        shop_service.add_to_cart(alice, "featherbook-14", quantity=1)

        preview = graphql(COUPON_PREVIEW, alice, code="WELCOME")["data"]["shopCouponPreview"]

        assert preview["isValid"]

    def test_an_unknown_code_answers_rather_than_erroring(self, alice: Any) -> None:
        body = graphql(COUPON_PREVIEW, alice, code="nonesuch")

        assert not body.get("errors"), body
        assert body["data"]["shopCouponPreview"]["reason"] == "No such code."


class TestOverGrpc:
    def test_the_address_book_reads_back(
        self, transactional_db: None, alice: Any, address: Address, grpc_call: Callable[..., Any]
    ) -> None:
        reply = grpc_call(Stub, "Addresses", Empty(), token=access_token(alice))

        assert [row.city for row in reply.addresses] == ["Bristol"]

    def test_it_needs_a_credential(
        self, transactional_db: None, address: Address, grpc_call: Callable[..., Any]
    ) -> None:
        with pytest.raises(grpc.RpcError) as refused:
            grpc_call(Stub, "Addresses", Empty())

        assert refused.value.code() == grpc.StatusCode.UNAUTHENTICATED

    def test_an_address_is_saved_defaulted_and_forgotten(
        self, transactional_db: None, alice: Any, grpc_call: Callable[..., Any]
    ) -> None:
        token = access_token(alice)
        saved = grpc_call(
            Stub,
            "AddAddress",
            shop_pb2.AddAddressRequest(
                full_name=NEW["full_name"],
                phone=NEW["phone"],
                country=NEW["country"],
                city=NEW["city"],
                postal_code=NEW["postal_code"],
                line1=NEW["line1"],
            ),
            token=token,
        ).address
        assert saved.city == "Bath"

        chosen = grpc_call(
            Stub, "SetDefaultAddress", shop_pb2.AddressRequest(address_id=saved.id), token=token
        ).address
        assert chosen.is_default

        gone = grpc_call(
            Stub, "RemoveAddress", shop_pb2.RemoveAddressRequest(address_id=saved.id), token=token
        )
        assert gone.deleted

    def test_the_delivery_options_are_public(
        self, transactional_db: None, shipping: ShippingMethod, grpc_call: Callable[..., Any]
    ) -> None:
        reply = grpc_call(Stub, "ShippingMethods", Empty())

        assert [row.name for row in reply.methods] == ["Standard"]

    def test_a_coupon_is_previewed(
        self,
        transactional_db: None,
        alice: Any,
        coupon: Coupon,
        laptop: Product,
        grpc_call: Callable[..., Any],
    ) -> None:
        shop_service.add_to_cart(alice, "featherbook-14", quantity=1)

        preview = grpc_call(
            Stub,
            "PreviewCoupon",
            shop_pb2.CouponPreviewRequest(code="WELCOME"),
            token=access_token(alice),
        ).preview

        assert preview.is_valid
        assert Decimal(preview.discount) == Decimal("60.00")
