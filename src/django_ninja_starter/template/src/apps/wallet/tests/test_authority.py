"""Who is allowed to say that money moved.

The security question this app exists to get right, and the one it got wrong.
Settling, failing, expiring and reversing were published to the account itself,
which meant a customer could confirm their own deposit: two calls, and a balance
of a million. These tests are the fence, written so that putting any of those
verbs back on the customer's API turns one of them red.

The rule, stated once: **a movement's lifecycle is the outside world's to
report, and the outside world is not the account.** The only verb an account
gets is `cancel`, because giving up on a payment you started asserts nothing
about whether money moved.
"""

import json
from decimal import Decimal
from typing import Any

import pytest
from django.contrib.auth import get_user_model
from django.test import Client
from django.urls import reverse

from apps.wallet import hooks
from apps.wallet.catalog import MethodCurrency, PaymentMethod
from apps.wallet.models import EntryStatus, WalletEntry
from apps.wallet.services import wallet_service
from apps.wallet.tests.conftest import bearer

pytestmark = pytest.mark.django_db

SECRET = "whsec-test-secret"


@pytest.fixture
def mallory(db: None) -> Any:
    """An ordinary account. Not staff, not an operator -- a customer."""
    return get_user_model().objects.create_user(username="mallory", email="m@example.test")


@pytest.fixture
def instant(db: None) -> PaymentMethod:
    """A rail that settles the moment it is used, with approval switched off.

    The worst case on purpose: every safety this app has, turned off by an
    administrator who was told it was right for their processor.
    """
    return _method("counter", "cash", approval=False)


@pytest.fixture
def rail(db: None) -> PaymentMethod:
    """A rail that waits for its processor -- the ordinary card or bank case."""
    return _method("card", "card", approval=False)


def _method(code: str, kind: str, *, approval: bool) -> PaymentMethod:
    method = PaymentMethod.objects.create(
        code=code,
        name=code,
        rail=kind,
        is_enabled=True,
        supports_deposit=True,
        supports_withdrawal=True,
        requires_approval=approval,
    )
    MethodCurrency.objects.create(method=method, currency="USD", is_enabled=True)
    return method


def _client() -> Client:
    return Client()


# -- what the account may not do ----------------------------------------------


@pytest.mark.parametrize(
    "verb,body",
    [
        ("settle", {"external_reference": "i-am-the-bank"}),
        ("fail", {"reason": "no"}),
        ("expire", {}),
        ("reverse", {"reference": "undo"}),
    ],
)
def test_the_account_cannot_report_its_own_movement(
    mallory: Any, rail: PaymentMethod, verb: str, body: dict[str, Any]
) -> None:
    """None of these is a thing the account knows, so none is published to it.

    A 404 from the router rather than a refusal from the service: the endpoint
    is not there at all, which is the only version of this that cannot be got
    wrong again by a later change to a permission check.
    """
    entry = wallet_service.deposit(
        mallory, amount=Decimal("500"), method="card", reference="pending-one"
    )

    response = _client().post(
        f"/api/v1/wallet/entries/{entry['id']}/{verb}",
        body,
        content_type="application/json",
        **bearer(mallory),
    )

    assert response.status_code == 404, f"/{verb} is still published to the account"


def test_the_account_cannot_credit_itself_through_an_instant_rail(
    mallory: Any, instant: PaymentMethod
) -> None:
    """The deposit is still recorded -- recording is the account's to do.

    What it may not do is arrive: an instant rail with approval off is the one
    configuration where the entry lands settled, and that is an administrator
    deciding a counter clerk's word is good enough, not a customer deciding it.
    So this asserts the ceiling rather than a refusal: whatever the deposit does,
    nobody can move it afterwards without a signature or an operator.
    """
    client = _client()
    entry = client.post(
        "/api/v1/wallet/deposits",
        {"amount": "1000000", "method": "counter", "reference": "free-money"},
        content_type="application/json",
        **bearer(mallory),
    ).json()["data"]

    moved = client.post(
        f"/api/v1/wallet/entries/{entry['id']}/reverse",
        {"reference": "and-again"},
        content_type="application/json",
        **bearer(mallory),
    )

    assert moved.status_code == 404


def test_cancelling_is_still_the_account_s_own(mallory: Any, rail: PaymentMethod) -> None:
    """The one verb that stays, because it claims nothing about the world."""
    entry = wallet_service.deposit(
        mallory, amount=Decimal("500"), method="card", reference="changed-my-mind"
    )

    response = _client().post(
        f"/api/v1/wallet/entries/{entry['id']}/cancel",
        {"reason": "wrong card"},
        content_type="application/json",
        **bearer(mallory),
    )

    assert response.status_code == 200
    assert response.json()["data"]["status"] == "cancelled"


def test_the_graph_publishes_no_lifecycle_verb_either(mallory: Any) -> None:
    """Three transports, one contract -- including what the contract leaves out."""
    from apps.wallet.graph import schema as wallet_schema

    published = {
        name
        for name in dir(wallet_schema.Mutation)
        if name.startswith("wallet_") and not name.startswith("_")
    }

    assert "wallet_cancel" in published
    for gone in ("wallet_settle", "wallet_fail", "wallet_reverse"):
        assert gone not in published, f"{gone} is still a mutation"


# -- what the rail may do, having proved who it is ----------------------------


def _signed(entry_id: Any, event: str, *, secret: str = SECRET, **extra: Any) -> Any:
    body = json.dumps({"entry_id": str(entry_id), "event": event, **extra}).encode()
    return _client().post(
        "/api/v1/wallet/hooks/card",
        body,
        content_type="application/json",
        headers=hooks.headers_for(secret, body),
    )


@pytest.fixture
def signed(settings: Any) -> None:
    settings.WALLET_WEBHOOK_SECRETS = {"card": SECRET}


def test_a_signed_confirmation_settles_the_movement(
    mallory: Any, rail: PaymentMethod, signed: None
) -> None:
    """The replacement for the endpoint the customer used to be able to call."""
    entry = wallet_service.deposit(
        mallory, amount=Decimal("500"), method="card", reference="real-one"
    )

    response = _signed(entry["id"], "done", external_reference="ch_123")

    assert response.status_code == 200, response.content
    assert response.json()["data"]["status"] == "done"
    assert wallet_service.balance(mallory)["settled"] == Decimal("500.0000")


def test_an_unsigned_confirmation_is_refused(
    mallory: Any, rail: PaymentMethod, signed: None
) -> None:
    entry = wallet_service.deposit(
        mallory, amount=Decimal("500"), method="card", reference="real-one"
    )

    response = _client().post(
        "/api/v1/wallet/hooks/card",
        {"entry_id": entry["id"], "event": "done"},
        content_type="application/json",
    )

    assert response.status_code == 401
    assert wallet_service.balance(mallory)["settled"] == Decimal("0.0000")


def test_a_confirmation_signed_with_the_wrong_secret_is_refused(
    mallory: Any, rail: PaymentMethod, signed: None
) -> None:
    """Which is what a customer who read the documentation would try."""
    entry = wallet_service.deposit(
        mallory, amount=Decimal("500"), method="card", reference="real-one"
    )

    response = _signed(entry["id"], "done", secret="guessed")

    assert response.status_code == 401
    assert wallet_service.balance(mallory)["settled"] == Decimal("0.0000")


def test_a_stale_confirmation_cannot_be_replayed(
    mallory: Any, rail: PaymentMethod, signed: None, settings: Any
) -> None:
    """A correctly signed request, captured and sent back an hour later."""
    import time

    entry = wallet_service.deposit(
        mallory, amount=Decimal("500"), method="card", reference="real-one"
    )
    body = json.dumps({"entry_id": str(entry["id"]), "event": "done"}).encode()
    old = hooks.headers_for(SECRET, body, now=time.time() - 3600)

    response = _client().post(
        "/api/v1/wallet/hooks/card", body, content_type="application/json", headers=old
    )

    assert response.status_code == 401


def test_a_method_with_no_secret_confirms_nothing(mallory: Any, rail: PaymentMethod) -> None:
    """The safe direction to fail: a queue somebody notices, not an open door."""
    entry = wallet_service.deposit(
        mallory, amount=Decimal("500"), method="card", reference="real-one"
    )

    response = _signed(entry["id"], "done")

    assert response.status_code == 401


def test_one_rail_cannot_confirm_another_rail_s_movement(
    mallory: Any, rail: PaymentMethod, settings: Any
) -> None:
    """A leaked secret should be one processor's problem, not every processor's."""
    _method("bank", "bank_transfer", approval=False)
    settings.WALLET_WEBHOOK_SECRETS = {"card": SECRET, "bank": "other-secret"}
    entry = wallet_service.deposit(
        mallory, amount=Decimal("500"), method="bank", reference="a-bank-one"
    )

    # Signed correctly -- for the card rail, about a bank movement.
    response = _signed(entry["id"], "done")

    assert response.status_code == 404
    assert wallet_service.balance(mallory)["settled"] == Decimal("0.0000")


def test_a_rail_cannot_settle_past_an_operator(mallory: Any, settings: Any) -> None:
    """Approval and confirmation are different facts; the rail supplies one."""
    _method("supervised", "card", approval=True)
    settings.WALLET_WEBHOOK_SECRETS = {"supervised": SECRET}
    entry = wallet_service.deposit(
        mallory, amount=Decimal("500"), method="supervised", reference="needs-a-person"
    )
    body = json.dumps({"entry_id": str(entry["id"]), "event": "done"}).encode()

    response = _client().post(
        "/api/v1/wallet/hooks/supervised",
        body,
        content_type="application/json",
        headers=hooks.headers_for(SECRET, body),
    )

    assert response.status_code == 409
    assert wallet_service.balance(mallory)["settled"] == Decimal("0.0000")


def test_a_rail_repeating_itself_is_not_an_error(
    mallory: Any, rail: PaymentMethod, signed: None
) -> None:
    """Because rails repeat themselves, and a 409 would be retried forever."""
    entry = wallet_service.deposit(
        mallory, amount=Decimal("500"), method="card", reference="real-one"
    )

    first = _signed(entry["id"], "done", external_reference="ch_1")
    again = _signed(entry["id"], "done", external_reference="ch_1")

    assert first.status_code == 200
    assert again.status_code == 200
    assert wallet_service.balance(mallory)["settled"] == Decimal("500.0000")
    assert WalletEntry.objects.filter(status=str(EntryStatus.DONE)).count() == 1


def test_a_rail_may_only_report_the_two_things_it_knows(
    mallory: Any, rail: PaymentMethod, signed: None
) -> None:
    """`reversed` is a decision, not an observation, and is refused here."""
    entry = wallet_service.deposit(
        mallory, amount=Decimal("500"), method="card", reference="real-one"
    )

    response = _signed(entry["id"], "reversed")

    assert response.status_code == 400


def test_the_hook_is_not_reachable_as_a_logged_in_account(
    mallory: Any, rail: PaymentMethod, signed: None
) -> None:
    """A bearer token buys nothing here: the signature is the only credential."""
    entry = wallet_service.deposit(
        mallory, amount=Decimal("500"), method="card", reference="real-one"
    )

    response = _client().post(
        "/api/v1/wallet/hooks/card",
        {"entry_id": entry["id"], "event": "done"},
        content_type="application/json",
        **bearer(mallory),
    )

    assert response.status_code == 401


def test_the_admin_can_still_settle_by_hand(mallory: Any, rail: PaymentMethod) -> None:
    """The other door, for the deployment whose rail has no webhook at all."""
    entry = wallet_service.deposit(
        mallory, amount=Decimal("500"), method="card", reference="by-hand"
    )

    settled = wallet_service.settle(mallory, entry["id"])

    assert settled["status"] == "done"


def test_the_hook_endpoint_is_documented(mallory: Any) -> None:
    """It is part of the contract, so it belongs in the OpenAPI document."""
    document = _client().get(reverse("api-v1:openapi-json")).json()

    assert "/api/v1/wallet/hooks/{method_code}" in document["paths"]
