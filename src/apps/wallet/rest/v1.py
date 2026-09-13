"""The wallet over HTTP: the balance, the history, and every way money moves.

Every decision is :class:`WalletService`'s -- including the one that matters,
which is that the wallet is derived from the caller and there is no parameter
that can widen it. No endpoint here takes a wallet id.

The verbs mirror the GraphQL mutations and the gRPC calls one for one, under the
same names and with the same replies, so a project publishing all three publishes
one contract three ways.
"""

from decimal import Decimal
from typing import Any
from uuid import UUID

from django.contrib.auth import get_user_model
from django.http import HttpRequest
from ninja import Router
from ninja.errors import HttpError

from apps.wallet.methods import Direction, Method
from apps.wallet.models import EntryKind, EntryStatus
from apps.wallet.rest.hooks import router as hooks_router
from apps.wallet.rest.schemas import (
    BalanceOut,
    CheckpointOut,
    EntryOut,
    EntryPage,
    ExchangeOut,
    MethodOut,
    MoveIn,
    QuoteIn,
    QuoteOut,
    RateOut,
    ReasonIn,
    TransferIn,
    WalletOut,
)
from apps.wallet.services import WalletError, page_size, wallet_service

try:  # pragma: no cover - exercised by whichever branch the project installs
    from infrastructure.auth.core.sessions import api_auth
except ImportError:  # pragma: no cover - only in a project without the auth apps
    from ninja.security import django_auth as api_auth  # type: ignore[assignment]

router = Router(auth=api_auth)

# The rail's own door, mounted under this one so a deployment publishes a single
# wallet prefix. It authenticates by signature rather than by account -- see
# `apps.wallet.rest.hooks` -- which is why it is a router of its own rather than
# an endpoint here with `auth=None` quietly set on it.
router.add_router("/hooks", hooks_router)


def _run(call: Any, *args: Any, **kwargs: Any) -> Any:
    """Run one service call, translating its refusal into the status it carries.

    Every refusal this app makes is a :class:`WalletError` with a status on it,
    so the mapping lives on the exception rather than as a chain of `except`
    clauses that each transport has to keep in step.
    """
    try:
        return call(*args, **kwargs)
    except WalletError as refusal:
        raise HttpError(refusal.status, str(refusal)) from None


@router.get("", response=WalletOut, summary="This account's wallet and its balance")
def get_wallet(request: HttpRequest) -> dict[str, Any]:
    """The wallet screen in one call: the wallet, and every number about it.

    Opens a wallet the first time an account asks, unless the deployment set
    `DJANGO_WALLET_AUTO_CREATE=false`.
    """
    return _run(wallet_service.summary, request.user)


@router.get("/balance", response=BalanceOut, summary="What is there, and what is on its way")
def get_balance(request: HttpRequest) -> dict[str, Any]:
    """Never one number.

    `settled` is what the wallet holds; `available` is what may be spent, which
    is `settled` less anything a pending withdrawal has already claimed;
    `projected` is where it lands if everything outstanding succeeds. Authorise
    against `available`.
    """
    return _run(wallet_service.balance, request.user)


@router.get("/methods", response=list[MethodOut], summary="The ways to pay this deployment offers")
def list_methods(
    request: HttpRequest, direction: Direction | None = None, currency: str | None = None
) -> list[dict[str, Any]]:
    """Everything needed to render a payment screen, and the only place to learn it.

    The methods here are rows an administrator configured, not a list compiled
    into the app -- so a client that hard-codes one breaks the afternoon somebody
    turns a method on. Each carries its currencies, their limits, the chains a
    crypto asset moves on, and what it charges.

    Three fields drive most of a UI. `settles_immediately` says whether money is
    there at once or waits on a confirmation. `requires_approval` says whether
    this will be a request until an operator applies it -- worth telling the
    customer before they commit, not after. `needs_network` says whether they must
    pick a chain, which they must never be allowed to guess at.

    `?direction=debit` narrows to what can pay out; `?currency=EUR` to what takes
    euros.
    """
    return wallet_service.methods(direction.value if direction else None, currency=currency)


@router.get("/methods/{code}", response=MethodOut, summary="One way to pay, in full")
def get_method(
    request: HttpRequest, code: str, direction: Direction | None = None
) -> dict[str, Any]:
    """One method with its currencies, chains and charges."""
    return _run(wallet_service.method, code, direction=direction.value if direction else None)


@router.post("/quotes", response=QuoteOut, summary="What a movement would cost")
def quote(request: HttpRequest, payload: QuoteIn) -> dict[str, Any]:
    """Price a movement before making one. Nothing is written.

    Worth calling before every deposit and withdrawal, because the answer is
    exactly what the movement will do: the same function prices both, so an amount
    quoted here is the amount charged. It is also the cheap way to discover that
    an amount is outside a method's limits, before asking a customer to confirm
    something that is about to be refused.
    """
    return _run(
        wallet_service.quote,
        request.user,
        method=payload.method,
        direction=payload.direction.value,
        amount=payload.amount,
        currency=payload.currency,
        network=payload.network,
    )


@router.get("/rates", response=list[RateOut], summary="The conversion rates in force")
def list_rates(
    request: HttpRequest, base: str | None = None, quote: str | None = None
) -> list[dict[str, Any]]:
    """What this deployment converts at, and the spread it keeps on doing so.

    The margin is published beside the rate rather than folded into it. A
    deployment keeping a spread is entitled to one; a deployment that will not say
    it is keeping one is a different thing.
    """
    return wallet_service.rates(base=base, quote=quote)


@router.get("/exchange", response=ExchangeOut, summary="Convert an amount between currencies")
def exchange(
    request: HttpRequest,
    amount: Decimal,
    base: str,
    quote: str,
    direction: Direction | None = None,
) -> dict[str, Any]:
    """A calculator. No wallet is touched and nothing is written.

    `direction` picks which side of the spread to quote: `credit` for what you
    would receive, `debit` for what you would have to pay. It defaults to
    `credit`, which is the question people usually mean.
    """
    return _run(
        wallet_service.exchange,
        amount=amount,
        base=base,
        quote=quote,
        direction=direction.value if direction else str(Direction.CREDIT),
    )


@router.get("/entries", response=EntryPage, summary="Every movement this wallet has had")
def list_entries(
    request: HttpRequest,
    kind: EntryKind | None = None,
    status: EntryStatus | None = None,
    method: Method | None = None,
    direction: Direction | None = None,
    limit: int | None = None,
    offset: int = 0,
) -> dict[str, Any]:
    """Newest first, including what failed.

    A customer asking why a deposit never arrived is asking about exactly the
    rows a history of successes would have dropped, so `failed`, `cancelled` and
    `expired` entries are listed like any other. `total` counts everything
    matching the same filters, so a client can page without a second call.
    """
    filters: dict[str, Any] = {
        "kind": kind,
        "status": status,
        "method": method,
        "direction": direction,
    }
    return {
        "entries": _run(
            wallet_service.entries, request.user, limit=limit, offset=offset, **filters
        ),
        "total": _run(wallet_service.count, request.user, **filters),
        # The page served, not the page asked for: the deployment's ceiling may
        # have clamped it, and a client paging on the number it sent would step
        # straight over the rows it never saw.
        "limit": page_size(limit),
        "offset": offset,
    }


@router.get(
    "/checkpoints", response=list[CheckpointOut], summary="The archive the balance is read from"
)
def list_checkpoints(request: HttpRequest, limit: int | None = None) -> list[dict[str, Any]]:
    """Each folded run of entries and the balance it left.

    Published on purpose: it is what makes a derived balance checkable. Add the
    checkpoints up, add the entries written since the last one, and you get the
    number this API reports.
    """
    return _run(wallet_service.checkpoints, request.user, limit=limit)


@router.post("/deposits", response=EntryOut, summary="Record money arriving")
def deposit(request: HttpRequest, payload: MoveIn) -> dict[str, Any]:
    """Put money in, priced, in whatever state the method leaves it.

    `amount` is what the customer is paying in, before charges -- the figure they
    typed. What reaches the wallet is that less the fees, converted into the
    wallet's currency if `currency` names a different one. Call `/wallet/quotes`
    first to show them all of that before they commit.

    Where it ends up depends on two independent things. A cash or voucher deposit
    comes back `done`; a card, bank or crypto deposit comes back `pending` until
    something confirms it through `/entries/{id}/settle`. And if the method
    requires approval, it comes back `pending` with `awaiting_approval` set
    whatever its rail does -- it is a request until an operator applies it, and it
    cannot settle before they do.

    `reference` is yours and must be unique for this wallet: send the same one
    twice and you get the first entry back rather than a second deposit.
    """
    return _run(
        wallet_service.deposit,
        request.user,
        amount=payload.amount,
        method=payload.method,
        reference=payload.reference,
        currency=payload.currency,
        network=payload.network,
        external_reference=payload.external_reference,
        description=payload.description,
        metadata=payload.metadata,
    )


@router.post("/withdrawals", response=EntryOut, summary="Record money leaving")
def withdraw(request: HttpRequest, payload: MoveIn) -> dict[str, Any]:
    """Take money out, if it is there to take.

    `amount` is what leaves the wallet; the charges come out of it, so the
    customer receives the remainder. `/wallet/quotes` says exactly how much that
    is before you ask them to confirm.

    Checked against `available` rather than `settled`, under the wallet's row
    lock: two withdrawals racing for the same balance are serialised, and the
    second one sees what the first one did. A payout on a rail that has to confirm
    comes back `pending`, and holds its own money in the meantime.

    A payout needs a `destination`, and for crypto it needs the right `network`
    too. An address on the wrong chain is refused here rather than sent, because
    the chain would not refuse it -- it would deliver the money to nobody.
    """
    return _run(
        wallet_service.withdraw,
        request.user,
        amount=payload.amount,
        method=payload.method,
        reference=payload.reference,
        currency=payload.currency,
        network=payload.network,
        destination=payload.destination,
        external_reference=payload.external_reference,
        description=payload.description,
        metadata=payload.metadata,
    )


@router.post("/transfers", response=EntryOut, summary="Move money to another account's wallet")
def transfer(request: HttpRequest, payload: TransferIn) -> dict[str, Any]:
    """Both sides in one transaction, settled at once: there is no rail to wait for.

    **Free.** No commission, no tax, no fixed cost and no spread -- the amount
    that leaves one wallet is the amount that arrives in the other, always. The
    money never leaves this app, so nothing was spent moving it, and there is no
    configuration that can add a charge here: fees against the internal rail are
    refused at the point somebody tries to save one.

    Nothing to approve either, for the same reason. Answers with the sending
    entry; its `counterparty_id` is the row written in the other wallet. Both
    wallets have to be in the same currency -- this app does not convert on a
    transfer, because a conversion nobody chose a rate for is a loss somebody
    discovers later.
    """
    recipient = get_user_model().objects.filter(pk=payload.to_user_id).first()
    if recipient is None:
        raise HttpError(404, "No such account.")
    return _run(
        wallet_service.transfer,
        request.user,
        to_user=recipient,
        amount=payload.amount,
        reference=payload.reference,
        description=payload.description,
        metadata=payload.metadata,
    )


@router.get("/entries/{entry_id}", response=EntryOut, summary="Read one movement")
def get_entry(request: HttpRequest, entry_id: UUID) -> dict[str, Any]:
    return _run(wallet_service.entry, request.user, entry_id)


@router.post("/entries/{entry_id}/cancel", response=EntryOut, summary="Withdraw it before it lands")
def cancel(request: HttpRequest, entry_id: UUID, payload: ReasonIn) -> dict[str, Any]:
    """Call off a movement of your own before it lands. Any money it held is released.

    The one lifecycle verb that belongs to the account, because it is the only
    one that asserts nothing about the outside world: giving up on a payment you
    started is a decision you are entitled to make.

    Everything else a movement can become -- settled, failed, expired, reversed --
    is a statement that money did or did not move somewhere this app cannot see.
    Those are not the account's to make: a customer who could call them would be
    confirming their own deposits. They arrive from the rail, signed, at
    `/wallet/hooks/{method}`, or from an operator in the admin.
    """
    return _run(wallet_service.cancel, request.user, entry_id, reason=payload.reason)
