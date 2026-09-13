"""The rail *types* this app understands, and what each one implies.

There are two halves to "how money moves", and keeping them apart is what makes
the rest of this app tractable.

**This file is the type system.** A card is not a bank transfer: one clears in
seconds and can be charged back for months, the other takes a day and is final.
That is a fact about cards, not a fact about a deployment, so it is declared in
code, reviewed in code, and cannot be edited by somebody filling in a form at two
in the morning.

**:mod:`apps.wallet.catalog` is the configuration.** *Which* card processor this
deployment runs, what it charges, which currencies it takes, whether a deposit
through it needs an operator to confirm -- those are commercial decisions that
change without a release, so they are rows an administrator fills in.

A configured method always points at a rail here, and inherits its physics.
Nothing in the admin can make a card irreversible or a cash payment asynchronous.

Each rail declares the facts the rest of the app branches on:

``family``
    What the customer recognises it as -- cash, a bank, a card, a wallet, crypto.
    Clients group by this, and the fields a customer has to supply follow from it.

``settles_immediately``
    Whether an entry made this way is done when written, or waits on something
    outside this app. Cash is; a card is not.

``reversible``
    Whether money that arrived this way can be taken back later by the
    counterparty. Nothing here enforces it -- it is what a risk rule reads to
    decide whether a balance is safe to pay out.

``directions``
    Which way it can carry money. Most carry both; a withdrawal "by voucher" is
    a different product rather than the same one backwards.

``needs_network`` / ``needs_destination``
    What a payout through it cannot be made without: a chain for crypto, an
    address or account number for anything that leaves this app.
"""

from dataclasses import dataclass

from django.db import models


class Direction(models.TextChoices):
    """Which way an entry moves the balance. Every entry is one or the other."""

    CREDIT = "credit", "In"
    DEBIT = "debit", "Out"


class Family(models.TextChoices):
    """What a customer would call this way of paying.

    Coarser than the rail on purpose: a client renders one "Bank" group whether
    the deployment runs SEPA, ACH or wires, and the customer picking one does not
    care which of the three the operator configured.
    """

    CASH = "cash", "Cash"
    BANK = "bank", "Bank"
    CARD = "card", "Card"
    WALLET = "wallet", "Wallet"
    CRYPTO = "crypto", "Crypto"
    VOUCHER = "voucher", "Voucher"
    INTERNAL = "internal", "Wallet to wallet"
    SYSTEM = "system", "System"


class Method(models.TextChoices):
    """Every rail this app knows how to record money arriving or leaving on.

    ``TextChoices`` rather than a plain enum so a model field, a filter, a
    schema and the admin all read the same list, and a value outside it is
    refused by the database rather than stored and puzzled over later.
    """

    CARD = "card", "Card"
    BANK_TRANSFER = "bank_transfer", "Bank transfer"
    SEPA = "sepa", "SEPA"
    ACH = "ach", "ACH"
    WIRE = "wire", "Wire"
    GATEWAY = "gateway", "Payment gateway"
    PAYPAL = "paypal", "PayPal"
    MOBILE_MONEY = "mobile_money", "Mobile money"
    CRYPTO = "crypto", "Crypto"
    CASH = "cash", "Cash"
    CHEQUE = "cheque", "Cheque"
    VOUCHER = "voucher", "Voucher"
    INTERNAL = "internal", "Another wallet"
    SYSTEM = "system", "System"


@dataclass(frozen=True)
class MethodSpec:
    """What one rail implies for the entries recorded against it."""

    method: str
    label: str
    family: str
    directions: frozenset[str]
    settles_immediately: bool
    reversible: bool
    needs_network: bool
    needs_destination: bool
    description: str

    def carries(self, direction: str) -> bool:
        return direction in self.directions

    @property
    def chargeable(self) -> bool:
        """Whether a deployment may attach fees to methods on this rail.

        False for the two rails that have no outside counterparty: a transfer
        between two wallets in this app and a correction made by the app itself
        both move money that never leaves, so there is no cost to pass on. See
        :mod:`apps.wallet.charges` for where this is enforced rather than assumed.
        """
        return self.method not in {str(Method.INTERNAL), str(Method.SYSTEM)}


BOTH = frozenset({str(Direction.CREDIT), str(Direction.DEBIT)})
IN_ONLY = frozenset({str(Direction.CREDIT)})
OUT_ONLY = frozenset({str(Direction.DEBIT)})


#: Every rail this app ships, keyed by its stored value. A deployment turns some
#: of them off; none of them are invented at runtime, because a rail nobody has
#: integrated is a support ticket rather than a feature.
METHODS: dict[str, MethodSpec] = {
    spec.method: spec
    for spec in (
        MethodSpec(
            method=str(Method.CARD),
            label="Card",
            family=str(Family.CARD),
            directions=BOTH,
            settles_immediately=False,
            reversible=True,
            needs_network=False,
            needs_destination=True,
            description=(
                "An authorisation the processor confirms, and can reverse for months "
                "afterwards. Deposits made this way stay pending until the capture "
                "webhook lands."
            ),
        ),
        MethodSpec(
            method=str(Method.BANK_TRANSFER),
            label="Bank transfer",
            family=str(Family.BANK),
            directions=BOTH,
            settles_immediately=False,
            reversible=False,
            needs_network=False,
            needs_destination=True,
            description="A push or a payout through a bank, confirmed when it clears.",
        ),
        MethodSpec(
            method=str(Method.SEPA),
            label="SEPA",
            family=str(Family.BANK),
            directions=BOTH,
            settles_immediately=False,
            reversible=False,
            needs_network=False,
            needs_destination=True,
            description="A euro-area credit transfer. Same shape as a bank transfer.",
        ),
        MethodSpec(
            method=str(Method.ACH),
            label="ACH",
            family=str(Family.BANK),
            directions=BOTH,
            settles_immediately=False,
            reversible=True,
            needs_network=False,
            needs_destination=True,
            description=(
                "A US batch transfer. Reversible, because an ACH debit can be returned "
                "days after it appeared to have cleared."
            ),
        ),
        MethodSpec(
            method=str(Method.WIRE),
            label="Wire",
            family=str(Family.BANK),
            directions=BOTH,
            settles_immediately=False,
            reversible=False,
            needs_network=False,
            needs_destination=True,
            description="A same-day wire. Slow to arrive, and final once it has.",
        ),
        MethodSpec(
            method=str(Method.GATEWAY),
            label="Payment gateway",
            family=str(Family.CARD),
            directions=BOTH,
            settles_immediately=False,
            reversible=True,
            needs_network=False,
            needs_destination=False,
            description=(
                "A hosted checkout or payout provider standing between this app and "
                "the rail. Confirmed by its webhook, disputable afterwards."
            ),
        ),
        MethodSpec(
            method=str(Method.PAYPAL),
            label="PayPal",
            family=str(Family.WALLET),
            directions=BOTH,
            settles_immediately=False,
            reversible=True,
            needs_network=False,
            needs_destination=True,
            description="A wallet-to-wallet transfer that can be disputed.",
        ),
        MethodSpec(
            method=str(Method.MOBILE_MONEY),
            label="Mobile money",
            family=str(Family.WALLET),
            directions=BOTH,
            settles_immediately=False,
            reversible=False,
            needs_network=False,
            needs_destination=True,
            description="A carrier wallet. Confirmed by callback, and final once confirmed.",
        ),
        MethodSpec(
            method=str(Method.CRYPTO),
            label="Crypto",
            family=str(Family.CRYPTO),
            directions=BOTH,
            settles_immediately=False,
            reversible=False,
            needs_network=True,
            needs_destination=True,
            description=(
                "An on-chain transfer. Pending until it has the confirmations the "
                "integration asks for, and irreversible after that. The same asset on "
                "two chains is two different things to pay to, which is what "
                "`MethodNetwork` exists to say."
            ),
        ),
        MethodSpec(
            method=str(Method.CASH),
            label="Cash",
            family=str(Family.CASH),
            directions=BOTH,
            settles_immediately=True,
            reversible=False,
            needs_network=False,
            needs_destination=False,
            description=(
                "Money handed over at a counter. Somebody has already counted it, so "
                "the entry is done the moment it is written."
            ),
        ),
        MethodSpec(
            method=str(Method.CHEQUE),
            label="Cheque",
            family=str(Family.BANK),
            directions=BOTH,
            settles_immediately=False,
            reversible=True,
            needs_network=False,
            needs_destination=True,
            description="Deposited on paper, and able to bounce after it appeared to clear.",
        ),
        MethodSpec(
            method=str(Method.VOUCHER),
            label="Voucher",
            family=str(Family.VOUCHER),
            directions=IN_ONLY,
            settles_immediately=True,
            reversible=False,
            needs_network=False,
            needs_destination=False,
            description=(
                "A code redeemed for value. One way on purpose: paying somebody out in "
                "vouchers is a different product, not this one backwards."
            ),
        ),
        MethodSpec(
            method=str(Method.INTERNAL),
            label="Another wallet",
            family=str(Family.INTERNAL),
            directions=BOTH,
            settles_immediately=True,
            reversible=False,
            needs_network=False,
            needs_destination=False,
            description=(
                "A transfer between two wallets in this app. Both sides are written in "
                "one transaction, so there is nothing left to confirm -- and nothing "
                "leaves, so there is no cost to pass on and no fee may be charged."
            ),
        ),
        MethodSpec(
            method=str(Method.SYSTEM),
            label="System",
            family=str(Family.SYSTEM),
            directions=BOTH,
            settles_immediately=True,
            reversible=False,
            needs_network=False,
            needs_destination=False,
            description=(
                "The app itself: a fee it charged, a bonus it granted, an operator "
                "correcting a mistake. There is no counterparty to wait for, and no "
                "charge to add on top."
            ),
        ),
    )
}


class UnknownMethod(LookupError):
    """A method this app does not implement."""


def spec(method: str) -> MethodSpec:
    """The declaration for one rail, or a refusal naming what there is."""
    try:
        return METHODS[method]
    except KeyError:
        raise UnknownMethod(
            f"Unknown wallet method {method!r}. Known: {', '.join(sorted(METHODS))}."
        ) from None


def enabled_methods() -> dict[str, MethodSpec]:
    """The rails this deployment allows, in declaration order.

    The outer gate. ``DJANGO_WALLET_METHODS`` says which rails a deployment has
    integrated at all; the catalog then says which configured methods run on
    them. A rail switched off here cannot be reached by adding a row.

    Read per call rather than captured at import, so ``override_settings`` works
    in a test and so a deployment narrowing the list does not need this module to
    have been imported after its settings.
    """
    from django.conf import settings

    allowed = set(getattr(settings, "WALLET_METHODS", ()) or METHODS)
    return {name: found for name, found in METHODS.items() if name in allowed}


def methods_for(direction: str) -> dict[str, MethodSpec]:
    """The enabled rails that can carry money in the given direction."""
    return {name: found for name, found in enabled_methods().items() if found.carries(direction)}
