"""The three transports publish the same movement, field for field.

This is the promise that rots quietly. A field added to the service payload
reaches whichever door somebody remembered, and the app then describes one
movement three different ways -- which is exactly the drift the shared
``entry_payload`` was written to prevent.

So it is asserted against the payload itself rather than against a list written
down here: a field added to the service is covered by this file the day it is
added, rather than the day somebody remembers to extend a fixture.
"""

from decimal import Decimal
from typing import Any

import pytest

from apps.wallet.catalog import PaymentMethod
from apps.wallet.graph.types import EntryType
from apps.wallet.grpc.serializers import Entry as GrpcEntry
from apps.wallet.models import WalletEntry
from apps.wallet.rest.schemas import EntryOut
from apps.wallet.services import entry_payload, page_size, wallet_service

pytestmark = pytest.mark.django_db


@pytest.fixture
def movement(alice: Any, cash: PaymentMethod) -> WalletEntry:
    """One entry that has been through the interesting half of its life.

    Reversed rather than merely settled, because that is the state where the
    fields disagree with each other: no longer `settled`, still counting, and
    still carrying the moment it cleared.
    """
    entry = wallet_service.deposit(alice, amount=Decimal("100"), method="cash", reference="d")
    wallet_service.reverse(alice, entry["id"], reference="rev")
    return WalletEntry.objects.get(pk=entry["id"])


def test_the_rest_schema_carries_every_field_the_service_produces(
    movement: WalletEntry,
) -> None:
    missing = set(entry_payload(movement)) - set(EntryOut.model_fields)
    assert not missing, f"the HTTP door drops {sorted(missing)}"


def test_the_graphql_type_carries_every_field_the_service_produces(
    movement: WalletEntry,
) -> None:
    # Strawberry stamps the definition on at decoration time, which the type
    # checker cannot see on the class itself.
    definition = EntryType.__strawberry_definition__  # type: ignore[attr-defined]
    published = {field.name for field in definition.fields}
    missing = set(entry_payload(movement)) - published
    assert not missing, f"the GraphQL door drops {sorted(missing)}"


def test_the_grpc_message_carries_every_field_the_service_produces(
    movement: WalletEntry,
) -> None:
    missing = set(entry_payload(movement)) - set(GrpcEntry().get_fields())
    assert not missing, f"the gRPC door drops {sorted(missing)}"


def test_the_balance_is_the_sum_of_the_movements_that_count(
    alice: Any, cash: PaymentMethod, card: PaymentMethod
) -> None:
    """The contract a ledger owes anybody reconciling against it.

    Stated here as arithmetic rather than as prose, over a wallet holding one of
    each interesting state: settled, reversed, failed and pending.
    """
    wallet_service.deposit(alice, amount=Decimal("500"), method="cash", reference="a")
    undone = wallet_service.deposit(alice, amount=Decimal("40"), method="cash", reference="b")
    wallet_service.reverse(alice, undone["id"], reference="rev")
    refused = wallet_service.deposit(alice, amount=Decimal("70"), method="card", reference="c")
    wallet_service.fail(alice, refused["id"])
    wallet_service.deposit(alice, amount=Decimal("25"), method="card", reference="d")

    rows = wallet_service.entries(alice, limit=200)
    counted = sum(
        (row["signed_amount"] for row in rows if row["counts_towards_balance"]), Decimal("0")
    )

    assert counted == wallet_service.balance(alice)["settled"] == Decimal("500.0000")


def test_a_listing_reports_the_page_it_served(alice: Any, cash: PaymentMethod) -> None:
    """A client paging on the number it *sent* would step over the clamped rows."""
    from django.test import override_settings

    for index in range(12):
        wallet_service.deposit(alice, amount=Decimal("1"), method="cash", reference=f"p{index}")

    with override_settings(WALLET_MAX_PAGE_SIZE=5):
        served = wallet_service.entries(alice, limit=5000)
        assert len(served) == page_size(5000) == 5


def test_a_record_and_the_queue_agree_about_what_is_waiting(
    alice: Any, bob: Any, counter: PaymentMethod
) -> None:
    """The model property and the queryset predicate are one rule, said twice.

    A row the queue excludes must not be a row the record describes as waiting --
    otherwise a client renders "awaiting approval" against a movement that was
    cancelled, and no operator can make that message go away.
    """
    live = wallet_service.deposit(alice, amount=Decimal("10"), method="counter", reference="a")
    dead = wallet_service.deposit(bob, amount=Decimal("20"), method="counter", reference="b")
    wallet_service.cancel(bob, dead["id"])

    queued = {row["id"] for row in wallet_service.awaiting_approval()}
    flagged = {
        row["id"]
        for user in (alice, bob)
        for row in wallet_service.entries(user)
        if row["awaiting_approval"]
    }

    assert queued == flagged == {live["id"]}
