"""The wallet over HTTP: the same rules, reached the way a client reaches them.

These go through the router rather than calling the service, because the bugs
worth catching here are the ones that live between the two -- a payload field
that never reaches the service, a refusal that arrives as a 500, an endpoint that
answers about somebody else's wallet.
"""

from decimal import Decimal
from typing import Any

import pytest
from django.test import Client

from apps.wallet.catalog import PaymentMethod
from apps.wallet.services import wallet_service
from apps.wallet.tests.conftest import bearer

pytestmark = pytest.mark.django_db

BASE = "/api/v1/wallet"


@pytest.fixture
def client() -> Client:
    return Client()


def body(response: Any) -> Any:
    """The payload inside this project's response envelope."""
    payload = response.json()
    return payload["data"] if isinstance(payload, dict) and "data" in payload else payload


def refusal(response: Any) -> str:
    """Everything the envelope said went wrong, as one searchable string."""
    payload = response.json()
    return " ".join(payload.get("errors") or []) + " " + str(payload.get("description") or "")


def test_the_wallet_screen_answers_with_every_number(
    client: Client, funded: Any, cash: PaymentMethod
) -> None:
    response = client.get(BASE, **bearer(funded))
    assert response.status_code == 200

    balance = body(response)["balance"]
    assert Decimal(str(balance["settled"])) == Decimal("1000.0000")
    # Never one number: a client shown a single figure guesses, and guesses kindly.
    assert {"settled", "available", "incoming", "outgoing", "projected"} <= balance.keys()


def test_the_methods_endpoint_carries_what_a_payment_screen_needs(
    client: Client, alice: Any, crypto: PaymentMethod, usdt_rate: object
) -> None:
    """Including the chains, because a client must never guess one."""
    response = client.get(f"{BASE}/methods", **bearer(alice))
    assert response.status_code == 200

    method = next(row for row in body(response) if row["code"] == "usdt")
    assert method["family"] == "crypto"
    assert method["needs_network"] is True
    chains = {net["code"] for net in method["currencies"][0]["networks"]}
    assert chains == {"trc20", "erc20"}


def test_a_quote_says_what_a_movement_will_cost_before_it_is_made(
    client: Client, alice: Any, card: PaymentMethod
) -> None:
    """And the number it gives is the number the deposit then charges."""
    response = client.post(
        f"{BASE}/quotes",
        data={"method": "card", "direction": "credit", "amount": "100"},
        content_type="application/json",
        **bearer(alice),
    )
    assert response.status_code == 200
    quoted = body(response)
    assert Decimal(str(quoted["fee_total"])) == Decimal("3.8400")
    assert Decimal(str(quoted["wallet_amount"])) == Decimal("96.1600")

    made = client.post(
        f"{BASE}/deposits",
        data={"method": "card", "amount": "100", "reference": "r"},
        content_type="application/json",
        **bearer(alice),
    )
    assert Decimal(str(body(made)["fee_total"])) == Decimal("3.8400")


def test_a_deposit_through_a_method_needing_approval_comes_back_as_a_request(
    client: Client, alice: Any, counter: PaymentMethod
) -> None:
    response = client.post(
        f"{BASE}/deposits",
        data={"method": "counter", "amount": "100", "reference": "r"},
        content_type="application/json",
        **bearer(alice),
    )
    assert response.status_code == 200
    entry = body(response)
    assert entry["awaiting_approval"] is True
    assert entry["status"] == "pending"


def test_the_account_is_not_offered_a_way_to_settle_its_own_movement(
    client: Client, alice: Any, counter: PaymentMethod
) -> None:
    """Not a refusal -- an absence. See `tests/test_authority.py` for why.

    This used to assert a 409 with the word "operator" in it, which read as a
    working control and was one only while the method required approval. The
    endpoint behind it let a customer confirm their own deposit as soon as an
    administrator turned that off, so it is gone: a rail confirms movements at
    `/wallet/hooks/{method}`, with a signature.
    """
    entry = wallet_service.deposit(alice, amount=Decimal("100"), method="counter", reference="r")
    response = client.post(
        f"{BASE}/entries/{entry['id']}/settle",
        data={},
        content_type="application/json",
        **bearer(alice),
    )
    assert response.status_code == 404


def test_a_withdrawal_carries_its_destination_and_chain(
    client: Client, funded: Any, crypto: PaymentMethod, usdt_rate: object
) -> None:
    response = client.post(
        f"{BASE}/withdrawals",
        data={
            "method": "usdt",
            "amount": "100",
            "currency": "USDT",
            "network": "trc20",
            "destination": "T" + "9" * 33,
            "reference": "w1",
        },
        content_type="application/json",
        **bearer(funded),
    )
    assert response.status_code == 200
    entry = body(response)
    assert entry["network"] == "trc20"
    assert Decimal(str(entry["fee_total"])) == Decimal("1.0000")


def test_a_payout_to_the_wrong_chain_is_refused_over_http(
    client: Client, funded: Any, crypto: PaymentMethod, usdt_rate: object
) -> None:
    response = client.post(
        f"{BASE}/withdrawals",
        data={
            "method": "usdt",
            "amount": "100",
            "currency": "USDT",
            "network": "trc20",
            "destination": "0x" + "a" * 40,
            "reference": "w1",
        },
        content_type="application/json",
        **bearer(funded),
    )
    assert response.status_code == 400
    assert "wrong chain" in refusal(response)


def test_the_exchange_endpoint_converts_without_touching_a_wallet(
    client: Client, alice: Any, eur_rate: object
) -> None:
    response = client.get(f"{BASE}/exchange?amount=100&base=EUR&quote=USD", **bearer(alice))
    assert response.status_code == 200
    assert Decimal(str(body(response)["converted"])) == Decimal("110.0000")


def test_the_rates_endpoint_publishes_the_spread_beside_the_rate(
    client: Client, alice: Any, usdt_rate: object
) -> None:
    response = client.get(f"{BASE}/rates", **bearer(alice))
    rate = body(response)[0]
    assert rate["base"] == "USDT"
    assert Decimal(str(rate["margin_percent"])) == Decimal("1.0000")


def test_a_wallet_is_never_reachable_by_a_parameter(
    client: Client, alice: Any, bob: Any, cash: PaymentMethod
) -> None:
    """There is no wallet id to get wrong, and an entry id from elsewhere is a 404.

    Deliberately a 404 rather than a 403: saying "forbidden" would confirm that
    somebody else's entry exists.
    """
    theirs = wallet_service.deposit(bob, amount=Decimal("10"), method="cash", reference="b")
    response = client.get(f"{BASE}/entries/{theirs['id']}", **bearer(alice))
    assert response.status_code == 404


def test_the_history_includes_what_failed(
    client: Client, alice: Any, card: PaymentMethod, cash: PaymentMethod
) -> None:
    """A customer asking why a deposit never arrived is asking about exactly these rows."""
    bad = wallet_service.deposit(alice, amount=Decimal("50"), method="card", reference="bad")
    wallet_service.fail(alice, bad["id"], reason="declined")
    wallet_service.deposit(alice, amount=Decimal("20"), method="cash", reference="good")

    response = client.get(f"{BASE}/entries", **bearer(alice))
    statuses = {row["status"] for row in body(response)["entries"]}
    assert statuses == {"failed", "done"}


def test_signing_in_is_required(client: Client, alice: Any) -> None:
    assert client.get(BASE).status_code == 401
