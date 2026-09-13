"""The same wallet over gRPC.

Every call reads its account out of ``authorization`` metadata, so there is no
field on any request that could name somebody else's wallet. That is the property
worth checking here, alongside the encoding decision that money travels as a
string — because a `double` would make 96.16 into something that is nearly 96.16,
and a ledger of nearly amounts holds nearly the right balance.

``transactional_db`` throughout: the server answers on its own connection, and
rows sitting in the test's open transaction are not there yet as far as it is
concerned.
"""

from collections.abc import Callable
from decimal import Decimal
from typing import Any

import grpc
import pytest
from google.protobuf.empty_pb2 import Empty

from apps.wallet.catalog import PaymentMethod
from apps.wallet.grpc import wallet_pb2, wallet_pb2_grpc
from apps.wallet.services import wallet_service
from apps.wallet.tests.conftest import access_token

Stub = wallet_pb2_grpc.WalletControllerStub


@pytest.fixture
def paying(transactional_db: None, alice: Any, card: PaymentMethod) -> Any:
    """Alice and a card method, on a connection the server can also see."""
    return alice


def test_the_balance_arrives_as_exact_strings(
    paying: Any, cash: PaymentMethod, grpc_call: Callable[..., Any]
) -> None:
    """Parsed back into a Decimal without ever having been a float."""
    wallet_service.deposit(paying, amount=Decimal("100"), method="cash", reference="d")

    reply = grpc_call(Stub, "GetWallet", Empty(), token=access_token(paying))

    assert Decimal(reply.wallet.balance.settled) == Decimal("100.0000")
    assert Decimal(reply.wallet.balance.available) == Decimal("100.0000")
    assert reply.wallet.currency == "USD"


def test_a_quote_says_what_a_deposit_will_cost(paying: Any, grpc_call: Callable[..., Any]) -> None:
    """The same arithmetic the other two transports get, to the last decimal."""
    reply = grpc_call(
        Stub,
        "PriceMovement",
        wallet_pb2.QuoteRequest(method="card", direction="credit", amount="100"),
        token=access_token(paying),
    )

    assert Decimal(reply.quote.fee_total) == Decimal("3.8400")
    assert Decimal(reply.quote.wallet_amount) == Decimal("96.1600")
    assert [charge.label for charge in reply.quote.charges] == ["Processing", "VAT"]


def test_a_deposit_carries_its_charges_back(paying: Any, grpc_call: Callable[..., Any]) -> None:
    reply = grpc_call(
        Stub,
        "Deposit",
        wallet_pb2.DepositRequest(method="card", amount="100", reference="r"),
        token=access_token(paying),
    )

    assert Decimal(reply.entry.gross_amount) == Decimal("100.0000")
    assert Decimal(reply.entry.amount) == Decimal("96.1600")
    assert reply.entry.status == "pending"


def test_the_methods_call_publishes_the_chains(
    transactional_db: None, alice: Any, crypto: PaymentMethod, grpc_call: Callable[..., Any]
) -> None:
    reply = grpc_call(Stub, "Methods", wallet_pb2.MethodsRequest(), token=access_token(alice))

    method = next(row for row in reply.methods if row.code == "usdt")
    assert method.needs_network is True
    assert {net.code for net in method.currencies[0].networks} == {"trc20", "erc20"}


def test_the_service_publishes_no_settle_call_at_all(
    transactional_db: None, alice: Any, counter: PaymentMethod
) -> None:
    """Three transports, one contract -- including what it leaves out.

    Settling was published here too, as the account, which meant a customer with
    a gRPC client could confirm their own deposit. It is the rail's statement
    now, made over a signed webhook; see `tests/test_authority.py`.
    """
    from apps.wallet.grpc.services import WalletService

    assert not hasattr(WalletService, "Settle")
    assert hasattr(WalletService, "Cancel")


def test_an_unsigned_call_is_refused(
    transactional_db: None, cash: PaymentMethod, grpc_call: Callable[..., Any]
) -> None:
    """There is no public wallet, so there is no anonymous read of one."""
    with pytest.raises(grpc.RpcError):
        grpc_call(Stub, "GetWallet", Empty())


def test_one_account_cannot_read_another_entry(
    transactional_db: None,
    alice: Any,
    bob: Any,
    cash: PaymentMethod,
    grpc_call: Callable[..., Any],
) -> None:
    theirs = wallet_service.deposit(bob, amount=Decimal("10"), method="cash", reference="b")

    with pytest.raises(grpc.RpcError) as refusal:
        grpc_call(
            Stub,
            "GetEntry",
            wallet_pb2.EntryRequest(entry_id=str(theirs["id"])),
            token=access_token(alice),
        )
    # Not found rather than forbidden: saying "forbidden" would confirm it exists.
    assert refusal.value.code() == grpc.StatusCode.NOT_FOUND
