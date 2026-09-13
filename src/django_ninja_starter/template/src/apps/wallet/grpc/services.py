"""The wallet over gRPC.

Every call derives its account from invocation metadata, so there is no field a
client could set to read or move somebody else's money -- the same rule the other
two transports follow, enforced the same way.

Applying a request is deliberately absent here, as it is from GraphQL. Approval
crosses wallets and its authorisation is the admin's, so it lives on the admin
screens and in :class:`~apps.wallet.services.WalletService`, where a project that
wants to publish it can reach it behind its own permissions.
"""

from __future__ import annotations

import json
from decimal import Decimal, InvalidOperation
from typing import Any

from asgiref.sync import sync_to_async
from django_socio_grpc import generics
from django_socio_grpc.decorators import grpc_action

from apps.wallet.grpc.serializers import (
    Checkpoint,
    Entry,
    Exchange,
    Method,
    Quote,
    Rate,
    Wallet,
)
from apps.wallet.services import WalletError, page_size, wallet_service
from infrastructure.common.errors import ApiError
from infrastructure.common.grpc.errors import action, require_caller
from infrastructure.common.identity import grpc_caller
from infrastructure.common.responses import ResponseTitle

TITLES = {
    400: ResponseTitle.VALIDATION_ERROR,
    404: ResponseTitle.NOT_FOUND,
    409: ResponseTitle.CONFLICT,
}


def _pb2() -> Any:
    from apps.wallet.grpc import wallet_pb2

    return wallet_pb2


def _refuse(error: WalletError) -> ApiError:
    """One refusal, carrying the status the exception declared.

    Decided on the exception rather than here, so this door's answer cannot drift
    from the HTTP one's.
    """
    status = getattr(error, "status", 400)
    return ApiError(
        str(error), status=status, title=TITLES.get(status, ResponseTitle.VALIDATION_ERROR)
    )


def _amount(value: str, field: str) -> Decimal:
    """One money-shaped field, parsed exactly, because protobuf has no decimal.

    Refused rather than coerced: a client sending something that is not a number
    should hear about it here, not have it become zero and move no money at all.
    """
    try:
        return Decimal(value)
    except (InvalidOperation, TypeError):
        raise ApiError(
            f"{field} has to be a number, written as a string: got {value!r}.",
            status=400,
            title=ResponseTitle.VALIDATION_ERROR,
        ) from None


def _text(value: Any) -> str:
    """Anything optional, as the empty string protobuf uses for "not set"."""
    return "" if value is None else str(value)


def _charge(row: dict[str, Any]) -> Any:
    return _pb2().Charge(
        kind=row["kind"],
        label=row["label"],
        percent=str(row["percent"]),
        amount=str(row["amount"]),
        absorbed=row["absorbed"],
    )


def _balance(row: dict[str, Any]) -> Any:
    return _pb2().Balance(
        currency=row["currency"],
        settled=str(row["settled"]),
        available=str(row["available"]),
        incoming=str(row["incoming"]),
        outgoing=str(row["outgoing"]),
        projected=str(row["projected"]),
        has_pending=row["has_pending"],
        checkpoint_sequence=row["checkpoint_sequence"],
        unarchived_entries=row["unarchived_entries"],
    )


def _wallet(row: dict[str, Any]) -> Any:
    return _pb2().Wallet(
        id=str(row["id"]),
        currency=row["currency"],
        status=row["status"],
        created_at=row["created_at"],
        balance=_balance(row["balance"]),
    )


def _entry(row: dict[str, Any]) -> Any:
    return _pb2().Entry(
        id=str(row["id"]),
        kind=row["kind"],
        direction=row["direction"],
        method=row["method"],
        payment_method=row["payment_method"],
        payment_method_name=row["payment_method_name"],
        network=row["network"],
        network_name=row["network_name"],
        amount=str(row["amount"]),
        signed_amount=str(row["signed_amount"]),
        currency=row["currency"],
        wallet_currency=row["wallet_currency"],
        gross_amount=str(row["gross_amount"]),
        fee_total=str(row["fee_total"]),
        net_amount=str(row["net_amount"]),
        charges=[_charge(charge) for charge in row["charges"]],
        converted=row["converted"],
        exchange_rate=_text(row["exchange_rate"]),
        destination=row["destination"],
        status=row["status"],
        settled=row["settled"],
        counts_towards_balance=row["counts_towards_balance"],
        approval=row["approval"],
        awaiting_approval=row["awaiting_approval"],
        reviewed_at=_text(row["reviewed_at"]),
        review_note=row["review_note"],
        reference=row["reference"],
        external_reference=row["external_reference"],
        description=row["description"],
        metadata=json.dumps(row["metadata"]),
        counterparty_id=_text(row["counterparty_id"]),
        archived=row["archived"],
        created_at=row["created_at"],
        settled_at=_text(row["settled_at"]),
    )


def _checkpoint(row: dict[str, Any]) -> Any:
    return _pb2().Checkpoint(
        id=str(row["id"]),
        sequence=row["sequence"],
        balance=str(row["balance"]),
        credited=str(row["credited"]),
        debited=str(row["debited"]),
        entry_count=row["entry_count"],
        created_at=row["created_at"],
    )


def _method(row: dict[str, Any]) -> Any:
    return _pb2().Method(
        code=row["code"],
        name=row["name"],
        rail=row["rail"],
        family=row["family"],
        description=row["description"],
        instructions=row["instructions"],
        icon=row["icon"],
        directions=row["directions"],
        settles_immediately=row["settles_immediately"],
        reversible=row["reversible"],
        requires_approval=row["requires_approval"],
        needs_network=row["needs_network"],
        needs_destination=row["needs_destination"],
        currencies=[
            _pb2().Currency(
                currency=asset["currency"],
                min_amount=str(asset["min_amount"]),
                max_amount=str(asset["max_amount"]),
                decimals=asset["decimals"],
                networks=[
                    _pb2().Network(
                        code=network["code"],
                        name=network["name"],
                        confirmations=network["confirmations"],
                        network_fee=str(network["network_fee"]),
                        min_amount=str(network["min_amount"]),
                        max_amount=str(network["max_amount"]),
                        deposit_address=network["deposit_address"],
                    )
                    for network in asset["networks"]
                ],
            )
            for asset in row["currencies"]
        ],
        fees=[
            _pb2().Fee(
                kind=fee["kind"],
                label=fee["label"],
                applies_to=fee["applies_to"],
                percent=str(fee["percent"]),
                fixed=str(fee["fixed"]),
                basis=fee["basis"],
                minimum=str(fee["minimum"]),
                maximum=str(fee["maximum"]),
                currency=fee["currency"],
            )
            for fee in row["fees"]
        ],
    )


def _exchange(row: dict[str, Any]) -> Any:
    return _pb2().Exchange(
        base=row["base"],
        quote=row["quote"],
        rate=str(row["rate"]),
        margin_percent=str(row["margin_percent"]),
        effective_rate=str(row["effective_rate"]),
        amount=str(row["amount"]),
        converted=str(row["converted"]),
        inverted=row["inverted"],
    )


def _rate(row: dict[str, Any]) -> Any:
    return _pb2().Rate(
        base=row["base"],
        quote=row["quote"],
        rate=str(row["rate"]),
        margin_percent=str(row["margin_percent"]),
        source=row["source"],
        effective_from=row["effective_from"],
    )


def _quote(row: dict[str, Any]) -> Any:
    return _pb2().Quote(
        method=row["method"],
        method_name=row["method_name"],
        rail=row["rail"],
        direction=row["direction"],
        currency=row["currency"],
        wallet_currency=row["wallet_currency"],
        network=row["network"],
        gross=str(row["gross"]),
        charges=[_charge(charge) for charge in row["charges"]],
        fee_total=str(row["fee_total"]),
        absorbed_total=str(row["absorbed_total"]),
        net=str(row["net"]),
        wallet_amount=str(row["wallet_amount"]),
        settlement_amount=str(row["settlement_amount"]),
        converted=row["converted"],
        exchange=_exchange(row["exchange"]),
        requires_approval=row["requires_approval"],
        settles_immediately=row["settles_immediately"],
    )


class WalletService(generics.GenericService):
    """One account's money: reading it, pricing a movement, and making one.

    The calls are named so that none of them collides with a message: the
    generator resolves actions and messages out of one registry, so a ``Quote``
    call beside a ``Quote`` message leaves the response type pointing at the
    call. Hence ``PriceMovement``, ``GetWallet``, ``GetEntry`` and ``Convert``.
    """

    @grpc_action(
        request=[],
        response=[{"name": "wallet", "type": Wallet}],
        response_name="WalletResult",
    )
    @action
    async def GetWallet(self, request: Any, context: Any) -> Any:
        """The wallet and every number about it. Authorise against `available`."""
        user = require_caller(await grpc_caller(context))
        try:
            row = await sync_to_async(wallet_service.summary)(user)
        except WalletError as refusal:
            raise _refuse(refusal) from None
        return _pb2().WalletResult(wallet=_wallet(row))

    @grpc_action(
        request=[
            {"name": "direction", "type": "string"},
            {"name": "currency", "type": "string"},
        ],
        request_name="MethodsRequest",
        response=[{"name": "methods", "cardinality": "repeated", "type": Method}],
        response_name="MethodList",
    )
    @action
    async def Methods(self, request: Any, context: Any) -> Any:
        """The ways to pay this deployment offers, with their limits and charges.

        Configuration rather than code, so this is the only place a client can
        learn them -- and a client that hard-codes a list breaks the afternoon an
        administrator turns one on.
        """
        require_caller(await grpc_caller(context))
        try:
            rows = await sync_to_async(wallet_service.methods)(
                request.direction or None, currency=request.currency or None
            )
        except WalletError as refusal:
            raise _refuse(refusal) from None
        return _pb2().MethodList(methods=[_method(row) for row in rows])

    @grpc_action(
        request=[
            {"name": "method", "type": "string"},
            {"name": "direction", "type": "string"},
            {"name": "amount", "type": "string"},
            {"name": "currency", "type": "string"},
            {"name": "network", "type": "string"},
        ],
        request_name="QuoteRequest",
        response=[{"name": "quote", "type": Quote}],
        response_name="QuoteResult",
    )
    @action
    async def PriceMovement(self, request: Any, context: Any) -> Any:
        """What a movement would cost. Nothing is written, and the figure holds."""
        user = require_caller(await grpc_caller(context))
        try:
            row = await sync_to_async(wallet_service.quote)(
                user,
                method=request.method,
                direction=request.direction,
                amount=_amount(request.amount, "amount"),
                currency=request.currency,
                network=request.network,
            )
        except WalletError as refusal:
            raise _refuse(refusal) from None
        return _pb2().QuoteResult(quote=_quote(row))

    @grpc_action(
        request=[
            {"name": "kind", "type": "string"},
            {"name": "status", "type": "string"},
            {"name": "method", "type": "string"},
            {"name": "direction", "type": "string"},
            {"name": "limit", "type": "int32"},
            {"name": "offset", "type": "int32"},
        ],
        request_name="EntriesRequest",
        response=[
            {"name": "entries", "cardinality": "repeated", "type": Entry},
            {"name": "total", "type": "int32"},
            {"name": "limit", "type": "int32"},
            {"name": "offset", "type": "int32"},
        ],
        response_name="EntryPage",
    )
    @action
    async def Entries(self, request: Any, context: Any) -> Any:
        """Every movement this wallet has had, newest first, including what failed."""
        user = require_caller(await grpc_caller(context))
        limit = request.limit or None
        filters = {
            "kind": request.kind or None,
            "status": request.status or None,
            "method": request.method or None,
            "direction": request.direction or None,
        }
        try:
            rows = await sync_to_async(wallet_service.entries)(
                user, limit=limit, offset=request.offset, **filters
            )
            total = await sync_to_async(wallet_service.count)(user, **filters)
        except WalletError as refusal:
            raise _refuse(refusal) from None
        return _pb2().EntryPage(
            entries=[_entry(row) for row in rows],
            total=total,
            # The page served, not the one asked for.
            limit=page_size(limit),
            offset=request.offset,
        )

    @grpc_action(
        request=[{"name": "entry_id", "type": "string"}],
        request_name="EntryRequest",
        response=[{"name": "entry", "type": Entry}],
        response_name="EntryResult",
    )
    @action
    async def GetEntry(self, request: Any, context: Any) -> Any:
        user = require_caller(await grpc_caller(context))
        try:
            row = await sync_to_async(wallet_service.entry)(user, request.entry_id)
        except WalletError as refusal:
            raise _refuse(refusal) from None
        return _pb2().EntryResult(entry=_entry(row))

    @grpc_action(
        request=[{"name": "limit", "type": "int32"}],
        request_name="CheckpointsRequest",
        response=[{"name": "checkpoints", "cardinality": "repeated", "type": Checkpoint}],
        response_name="CheckpointList",
    )
    @action
    async def Checkpoints(self, request: Any, context: Any) -> Any:
        """The archive the balance is read from, published so it can be checked."""
        user = require_caller(await grpc_caller(context))
        try:
            rows = await sync_to_async(wallet_service.checkpoints)(
                user, limit=request.limit or None
            )
        except WalletError as refusal:
            raise _refuse(refusal) from None
        return _pb2().CheckpointList(checkpoints=[_checkpoint(row) for row in rows])

    @grpc_action(
        request=[
            {"name": "base", "type": "string"},
            {"name": "quote", "type": "string"},
        ],
        request_name="RatesRequest",
        response=[{"name": "rates", "cardinality": "repeated", "type": Rate}],
        response_name="RateList",
    )
    @action
    async def Rates(self, request: Any, context: Any) -> Any:
        """The rates in force, with the spread published beside each rather than in it."""
        require_caller(await grpc_caller(context))
        rows = await sync_to_async(wallet_service.rates)(
            base=request.base or None, quote=request.quote or None
        )
        return _pb2().RateList(rates=[_rate(row) for row in rows])

    @grpc_action(
        request=[
            {"name": "amount", "type": "string"},
            {"name": "base", "type": "string"},
            {"name": "quote", "type": "string"},
            {"name": "direction", "type": "string"},
        ],
        request_name="ExchangeRequest",
        response=[{"name": "exchange", "type": Exchange}],
        response_name="ExchangeResult",
    )
    @action
    async def Convert(self, request: Any, context: Any) -> Any:
        """A calculator. No wallet is touched and nothing is written."""
        require_caller(await grpc_caller(context))
        try:
            row = await sync_to_async(wallet_service.exchange)(
                amount=_amount(request.amount, "amount"),
                base=request.base,
                quote=request.quote,
                direction=request.direction or "credit",
            )
        except WalletError as refusal:
            raise _refuse(refusal) from None
        return _pb2().ExchangeResult(exchange=_exchange(row))

    @grpc_action(
        request=[
            {"name": "amount", "type": "string"},
            {"name": "method", "type": "string"},
            {"name": "reference", "type": "string"},
            {"name": "currency", "type": "string"},
            {"name": "network", "type": "string"},
            {"name": "external_reference", "type": "string"},
            {"name": "description", "type": "string"},
        ],
        request_name="DepositRequest",
        response=[{"name": "entry", "type": Entry}],
        response_name="DepositResult",
    )
    @action
    async def Deposit(self, request: Any, context: Any) -> Any:
        """Record money arriving, priced. `reference` makes a retry safe."""
        user = require_caller(await grpc_caller(context))
        try:
            row = await sync_to_async(wallet_service.deposit)(
                user,
                amount=_amount(request.amount, "amount"),
                method=request.method,
                reference=request.reference,
                currency=request.currency,
                network=request.network,
                external_reference=request.external_reference,
                description=request.description,
            )
        except WalletError as refusal:
            raise _refuse(refusal) from None
        return _pb2().DepositResult(entry=_entry(row))

    @grpc_action(
        request=[
            {"name": "amount", "type": "string"},
            {"name": "method", "type": "string"},
            {"name": "reference", "type": "string"},
            {"name": "currency", "type": "string"},
            {"name": "network", "type": "string"},
            {"name": "destination", "type": "string"},
            {"name": "external_reference", "type": "string"},
            {"name": "description", "type": "string"},
        ],
        request_name="WithdrawRequest",
        response=[{"name": "entry", "type": Entry}],
        response_name="WithdrawResult",
    )
    @action
    async def Withdraw(self, request: Any, context: Any) -> Any:
        """Record money leaving, if it is there. Checked under the wallet's lock."""
        user = require_caller(await grpc_caller(context))
        try:
            row = await sync_to_async(wallet_service.withdraw)(
                user,
                amount=_amount(request.amount, "amount"),
                method=request.method,
                reference=request.reference,
                currency=request.currency,
                network=request.network,
                destination=request.destination,
                external_reference=request.external_reference,
                description=request.description,
            )
        except WalletError as refusal:
            raise _refuse(refusal) from None
        return _pb2().WithdrawResult(entry=_entry(row))

    @grpc_action(
        request=[
            {"name": "to_user_id", "type": "string"},
            {"name": "amount", "type": "string"},
            {"name": "reference", "type": "string"},
            {"name": "description", "type": "string"},
        ],
        request_name="TransferRequest",
        response=[{"name": "entry", "type": Entry}],
        response_name="TransferResult",
    )
    @action
    async def Transfer(self, request: Any, context: Any) -> Any:
        """Move money to another wallet here. Free: nothing leaves, so nothing is charged."""
        from django.contrib.auth import get_user_model

        user = require_caller(await grpc_caller(context))
        recipient = await sync_to_async(
            get_user_model().objects.filter(pk=request.to_user_id).first
        )()
        if recipient is None:
            raise ApiError("No such account.", status=404, title=ResponseTitle.NOT_FOUND)
        try:
            row = await sync_to_async(wallet_service.transfer)(
                user,
                to_user=recipient,
                amount=_amount(request.amount, "amount"),
                reference=request.reference,
                description=request.description,
            )
        except WalletError as refusal:
            raise _refuse(refusal) from None
        return _pb2().TransferResult(entry=_entry(row))

    @grpc_action(
        request=[
            {"name": "entry_id", "type": "string"},
            {"name": "reason", "type": "string"},
        ],
        request_name="CancelRequest",
        response=[{"name": "entry", "type": Entry}],
        response_name="CancelResult",
    )
    @action
    async def Cancel(self, request: Any, context: Any) -> Any:
        """Call off a movement of your own before it lands. Any money it held is released.

        The only lifecycle verb published to the account. Settling, failing and
        reversing say that money did or did not move somewhere this app cannot
        see, which is the rail's statement or an operator's -- never the
        account's own.
        """
        user = require_caller(await grpc_caller(context))
        try:
            row = await sync_to_async(wallet_service.cancel)(
                user, request.entry_id, reason=request.reason
            )
        except WalletError as refusal:
            raise _refuse(refusal) from None
        return _pb2().CancelResult(entry=_entry(row))


GRPC_SERVICES = [WalletService]
