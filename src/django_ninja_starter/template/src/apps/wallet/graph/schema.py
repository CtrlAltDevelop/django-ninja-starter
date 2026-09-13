"""The wallet's contribution to the project's GraphQL schema.

The same division as the routes, and the same rule underneath it: every field
resolves the wallet from the credential rather than from an argument, so there is
no identifier a caller could change to read or move somebody else's money.

Every field is prefixed ``wallet`` because the project merges each installed
app's ``Query`` into one root type, and a bare ``balance`` would collide with the
next app that holds money. The merge refuses collisions rather than silently
losing a field, so this is what keeps it quiet.

The resolvers are wrapped in ``@resolver``: the endpoint is one asynchronous view
and this service is ordinary synchronous Django, so it crosses over here, once,
rather than being written twice.

Applying a request is deliberately **not** here. Approval is the back office's
job, it crosses wallets, and the authorisation for it is the admin's -- so it
lives on the admin screens and in :class:`~apps.wallet.services.WalletService`,
where a project that wants to publish it can reach it with its own permissions.
"""

from decimal import Decimal
from typing import Any

import strawberry
from strawberry.types import Info

from apps.wallet.graph.types import (
    BalanceType,
    CheckpointType,
    EntryPageType,
    EntryType,
    ExchangeType,
    MethodType,
    QuoteType,
    RateType,
    WalletType,
    balance_type,
    checkpoint_type,
    entry_page_type,
    entry_type,
    exchange_type,
    method_type,
    quote_type,
    rate_type,
    wallet_type,
)
from apps.wallet.services import WalletError, WalletNotFound, page_size, wallet_service
from infrastructure.common.errors import ApiError
from infrastructure.common.graph.errors import require_caller, resolver
from infrastructure.common.identity import caller
from infrastructure.common.responses import ResponseTitle

TITLES = {
    400: ResponseTitle.VALIDATION_ERROR,
    404: ResponseTitle.NOT_FOUND,
    409: ResponseTitle.CONFLICT,
}


def _refuse(error: WalletError) -> ApiError:
    """One refusal, in the vocabulary the rest of the project uses.

    The status travels on the exception rather than being decided here, which is
    what keeps this transport's answer the same as the HTTP one's.
    """
    status = getattr(error, "status", 400)
    return ApiError(
        str(error), status=status, title=TITLES.get(status, ResponseTitle.VALIDATION_ERROR)
    )


def _caller(info: Info[Any, Any]) -> Any:
    """The account this query proves it is. Demanded: there is no public wallet."""
    return require_caller(caller(info.context.request))


def _run(call: Any, *args: Any, **kwargs: Any) -> Any:
    try:
        return call(*args, **kwargs)
    except WalletError as refusal:
        raise _refuse(refusal) from None


@strawberry.type
class Query:
    @strawberry.field(description="This account's wallet and every number about it.")
    @resolver
    def wallet(self, info: Info[Any, Any]) -> WalletType:
        return wallet_type(_run(wallet_service.summary, _caller(info)))

    @strawberry.field(
        description="What is there and what is on its way. Authorise against `available`."
    )
    @resolver
    def wallet_balance(self, info: Info[Any, Any]) -> BalanceType:
        return balance_type(_run(wallet_service.balance, _caller(info)))

    @strawberry.field(
        description="The ways to pay this deployment offers, with their limits, "
        "chains and charges. The only place a client can learn them."
    )
    @resolver
    def wallet_methods(
        self, info: Info[Any, Any], direction: str | None = None, currency: str | None = None
    ) -> list[MethodType]:
        _caller(info)
        return [
            method_type(row) for row in _run(wallet_service.methods, direction, currency=currency)
        ]

    @strawberry.field(description="One way to pay, in full.")
    @resolver
    def wallet_method(
        self, info: Info[Any, Any], code: str, direction: str | None = None
    ) -> MethodType:
        _caller(info)
        return method_type(_run(wallet_service.method, code, direction=direction))

    @strawberry.field(
        description="Every movement this wallet has had, newest first, including what failed."
    )
    @resolver
    def wallet_entries(
        self,
        info: Info[Any, Any],
        kind: str | None = None,
        status: str | None = None,
        method: str | None = None,
        direction: str | None = None,
        limit: int | None = None,
        offset: int = 0,
    ) -> EntryPageType:
        account = _caller(info)
        filters = {"kind": kind, "status": status, "method": method, "direction": direction}
        return entry_page_type(
            _run(wallet_service.entries, account, limit=limit, offset=offset, **filters),
            _run(wallet_service.count, account, **filters),
            page_size(limit),
            offset,
        )

    @strawberry.field(description="One movement out of this account's own wallet.")
    @resolver
    def wallet_entry(self, info: Info[Any, Any], entry_id: str) -> EntryType:
        return entry_type(_run(wallet_service.entry, _caller(info), entry_id))

    @strawberry.field(
        description="The archive the balance is read from: each folded run of "
        "entries and the balance it left."
    )
    @resolver
    def wallet_checkpoints(
        self, info: Info[Any, Any], limit: int | None = None
    ) -> list[CheckpointType]:
        return [
            checkpoint_type(row)
            for row in _run(wallet_service.checkpoints, _caller(info), limit=limit)
        ]

    @strawberry.field(
        description="What a movement would cost and produce. Nothing is written, "
        "and the figure quoted is the figure charged."
    )
    @resolver
    def wallet_quote(
        self,
        info: Info[Any, Any],
        method: str,
        direction: str,
        amount: Decimal,
        currency: str = "",
        network: str = "",
    ) -> QuoteType:
        return quote_type(
            _run(
                wallet_service.quote,
                _caller(info),
                method=method,
                direction=direction,
                amount=amount,
                currency=currency,
                network=network,
            )
        )

    @strawberry.field(description="The conversion rates in force, and the spread kept on them.")
    @resolver
    def wallet_rates(
        self, info: Info[Any, Any], base: str | None = None, quote: str | None = None
    ) -> list[RateType]:
        _caller(info)
        return [rate_type(row) for row in _run(wallet_service.rates, base=base, quote=quote)]

    @strawberry.field(description="Convert an amount between currencies. No wallet is touched.")
    @resolver
    def wallet_exchange(
        self,
        info: Info[Any, Any],
        amount: Decimal,
        base: str,
        quote: str,
        direction: str = "credit",
    ) -> ExchangeType:
        _caller(info)
        return exchange_type(
            _run(
                wallet_service.exchange, amount=amount, base=base, quote=quote, direction=direction
            )
        )


@strawberry.type
class Mutation:
    @strawberry.mutation(
        description="Record money arriving. `amount` is what the customer pays in, "
        "before charges; `reference` is your idempotency key."
    )
    @resolver
    def wallet_deposit(
        self,
        info: Info[Any, Any],
        amount: Decimal,
        method: str,
        reference: str,
        currency: str = "",
        network: str = "",
        external_reference: str = "",
        description: str = "",
    ) -> EntryType:
        return entry_type(
            _run(
                wallet_service.deposit,
                _caller(info),
                amount=amount,
                method=method,
                reference=reference,
                currency=currency,
                network=network,
                external_reference=external_reference,
                description=description,
            )
        )

    @strawberry.mutation(
        description="Record money leaving, if it is there to take. A payout needs "
        "a destination, and crypto needs the right chain with it."
    )
    @resolver
    def wallet_withdraw(
        self,
        info: Info[Any, Any],
        amount: Decimal,
        method: str,
        reference: str,
        currency: str = "",
        network: str = "",
        destination: str = "",
        external_reference: str = "",
        description: str = "",
    ) -> EntryType:
        return entry_type(
            _run(
                wallet_service.withdraw,
                _caller(info),
                amount=amount,
                method=method,
                reference=reference,
                currency=currency,
                network=network,
                destination=destination,
                external_reference=external_reference,
                description=description,
            )
        )

    @strawberry.mutation(
        description="Move money to another account's wallet here. Free: nothing "
        "leaves, so there is nothing to charge."
    )
    @resolver
    def wallet_transfer(
        self,
        info: Info[Any, Any],
        to_user_id: str,
        amount: Decimal,
        reference: str,
        description: str = "",
    ) -> EntryType:
        from django.contrib.auth import get_user_model

        recipient = get_user_model().objects.filter(pk=to_user_id).first()
        if recipient is None:
            raise _refuse(WalletNotFound("No such account."))
        return entry_type(
            _run(
                wallet_service.transfer,
                _caller(info),
                to_user=recipient,
                amount=amount,
                reference=reference,
                description=description,
            )
        )

    @strawberry.mutation(
        description="Call off a movement of your own before it lands -- the one "
        "lifecycle verb that belongs to the account, because it asserts nothing "
        "about whether money moved outside this app. Settling, failing and "
        "reversing come from the rail's signed webhook or from an operator."
    )
    @resolver
    def wallet_cancel(self, info: Info[Any, Any], entry_id: str, reason: str = "") -> EntryType:
        return entry_type(_run(wallet_service.cancel, _caller(info), entry_id, reason=reason))
