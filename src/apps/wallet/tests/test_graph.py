"""The same wallet over GraphQL.

Same service as the routes, so the same answers: the wallet is the caller's own
and there is no argument that widens it, a request cannot settle, and a refusal
arrives with the status and title the REST layer would have used -- in
``extensions``, since GraphQL answers 200 whatever happened.
"""

import json
from decimal import Decimal
from typing import Any

import pytest
from django.test import Client

from apps.wallet.catalog import PaymentMethod
from apps.wallet.services import wallet_service
from apps.wallet.tests.conftest import access_token

pytestmark = pytest.mark.django_db

WALLET = "{ wallet { currency status balance { settled available projected } } }"
METHODS = """
{
  walletMethods {
    code
    family
    requiresApproval
    needsNetwork
    currencies { currency minAmount networks { code networkFee } }
    fees { kind percent basis }
  }
}
"""
QUOTE = """
query($method: String!, $direction: String!, $amount: Decimal!) {
  walletQuote(method: $method, direction: $direction, amount: $amount) {
    feeTotal
    walletAmount
    charges { label amount }
  }
}
"""
DEPOSIT = """
mutation($method: String!, $amount: Decimal!, $reference: String!) {
  walletDeposit(method: $method, amount: $amount, reference: $reference) {
    id
    status
    approval
    awaitingApproval
    grossAmount
    feeTotal
    amount
    charges { label }
  }
}
"""
SETTLE = """
mutation($entryId: String!) {
  walletSettle(entryId: $entryId) { id status }
}
"""
TRANSFER = """
mutation($toUserId: String!, $amount: Decimal!, $reference: String!) {
  walletTransfer(toUserId: $toUserId, amount: $amount, reference: $reference) {
    amount
    feeTotal
    charges { label }
  }
}
"""
ENTRY = "query($entryId: String!) { walletEntry(entryId: $entryId) { id } }"


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


def refusal(body: dict[str, Any]) -> dict[str, Any]:
    assert body.get("errors"), body
    return dict(body["errors"][0]["extensions"])


def test_the_wallet_answers_with_every_number(funded: Any, cash: PaymentMethod) -> None:
    body = graphql(WALLET, funded)
    balance = body["data"]["wallet"]["balance"]

    assert Decimal(balance["settled"]) == Decimal("1000.0000")
    assert Decimal(balance["available"]) == Decimal("1000.0000")


def test_the_methods_field_carries_the_chains_and_the_charges(
    alice: Any, crypto: PaymentMethod, card: PaymentMethod, usdt_rate: Any
) -> None:
    body = graphql(METHODS, alice)
    methods = {row["code"]: row for row in body["data"]["walletMethods"]}

    assert methods["usdt"]["needsNetwork"] is True
    assert {net["code"] for net in methods["usdt"]["currencies"][0]["networks"]} == {
        "trc20",
        "erc20",
    }
    assert {fee["kind"] for fee in methods["card"]["fees"]} == {"commission", "tax"}


def test_a_quote_matches_what_the_deposit_then_charges(alice: Any, card: PaymentMethod) -> None:
    """One function prices both, so the two can only ever agree."""
    quoted = graphql(QUOTE, alice, method="card", direction="credit", amount="100")
    made = graphql(DEPOSIT, alice, method="card", amount="100", reference="r")

    assert Decimal(quoted["data"]["walletQuote"]["feeTotal"]) == Decimal("3.8400")
    assert Decimal(made["data"]["walletDeposit"]["feeTotal"]) == Decimal("3.8400")
    assert [charge["label"] for charge in made["data"]["walletDeposit"]["charges"]] == [
        "Processing",
        "VAT",
    ]


def test_a_deposit_needing_approval_comes_back_as_a_request(
    alice: Any, counter: PaymentMethod
) -> None:
    body = graphql(DEPOSIT, alice, method="counter", amount="100", reference="r")
    entry = body["data"]["walletDeposit"]

    assert entry["awaitingApproval"] is True
    assert entry["approval"] == "requested"
    assert entry["status"] == "pending"


def test_there_is_no_settle_mutation_for_an_account_to_call(
    alice: Any, counter: PaymentMethod
) -> None:
    """The schema does not carry it. See `tests/test_authority.py` for why.

    A mutation that let the account confirm its own movement was a mint behind a
    configuration flag; settling now arrives from the rail, signed, and nowhere
    else. GraphQL answers 200 with a query error, so that is what is asserted.
    """
    entry = wallet_service.deposit(alice, amount=Decimal("100"), method="counter", reference="r")

    body = graphql(SETTLE, alice, entryId=str(entry["id"]))

    assert body.get("data") is None
    assert "walletSettle" in str(body["errors"])


def test_a_transfer_is_free_here_too(funded: Any, bob: Any, cash: PaymentMethod) -> None:
    body = graphql(TRANSFER, funded, toUserId=str(bob.pk), amount="250", reference="t")
    sent = body["data"]["walletTransfer"]

    assert Decimal(sent["amount"]) == Decimal("250.0000")
    assert Decimal(sent["feeTotal"]) == Decimal("0.0000")
    assert sent["charges"] == []


def test_one_account_cannot_read_another_entry(alice: Any, bob: Any, cash: PaymentMethod) -> None:
    theirs = wallet_service.deposit(bob, amount=Decimal("10"), method="cash", reference="b")
    body = graphql(ENTRY, alice, entryId=str(theirs["id"]))

    assert refusal(body)["status"] == 404


def test_signing_in_is_required(db: None) -> None:
    body = graphql(WALLET)
    assert refusal(body)["status"] == 401
