"""Every refusal this app makes, and the HTTP status each one carries.

One exception hierarchy, in a module of its own, because three transports and
the pricing engine all raise from it and none of them should have to import a
service to do so.

The status lives on the exception rather than in a mapping at each door. A door
that has to decide which code a refusal deserves is a door that will eventually
disagree with the other two, and the caller who hits the disagreement is the one
writing the retry logic.
"""


class WalletError(Exception):
    """Something a caller asked for that this app will not do."""

    status = 400


class WalletNotFound(WalletError, LookupError):
    """No wallet, or an entry belonging to somebody else.

    Deliberately the same answer either way: saying which would confirm the
    existence of another account's money.
    """

    status = 404


class WalletFrozen(WalletError):
    """The wallet may not move that way right now."""

    status = 409


class InsufficientFunds(WalletError):
    """The money is not there, or is already spoken for by something pending."""

    status = 409


class MethodNotAllowed(WalletError):
    """A method this deployment does not run, or one that does not go that way."""

    status = 400


class CurrencyNotAllowed(WalletError):
    """A currency this method does not take."""

    status = 400


class NetworkRequired(WalletError):
    """A crypto movement without a chain, or with one that does not carry the asset.

    Its own class because it is the mistake that loses money rather than time:
    the same asset on the wrong chain is not a failed payment, it is a payment to
    an address nobody holds the key to.
    """

    status = 400


class NoExchangeRate(WalletError):
    """No live rate for the pair, so the conversion cannot be priced.

    A refusal rather than a guess. Converting at a stale rate, or at one, is how
    a deployment finds out it has been selling currency at yesterday's price.
    """

    status = 409


class InvalidAmount(WalletError):
    """Outside the limits configured for that method, currency or direction."""

    status = 400


class InvalidDestination(WalletError):
    """A payout with nowhere to go, or an address the chain would not accept."""

    status = 400


class InvalidTransition(WalletError):
    """An entry cannot get there from where it is."""

    status = 409


class ApprovalRequired(WalletError):
    """A movement an operator has not applied yet, asked to settle."""

    status = 409


class ReferenceReused(WalletError):
    """The same reference, asked to mean something different from last time.

    Idempotency is a promise that a retry is safe, and a retry is the *same*
    request sent again. A reference that comes back carrying a different amount
    is not a retry -- it is either a client generating references it has already
    used, or somebody probing what this app will let a reference mean. Returning
    the first entry to either of them is how a deposit of five thousand is
    silently recorded as five.
    """

    status = 409


class SignatureInvalid(WalletError):
    """A rail confirmation this deployment cannot prove came from the rail.

    Unsigned, wrongly signed, replayed, or for a method with no secret
    configured. All four are the same answer on purpose: an endpoint that
    distinguished them would tell whoever is probing it which half they got
    right.
    """

    status = 401
