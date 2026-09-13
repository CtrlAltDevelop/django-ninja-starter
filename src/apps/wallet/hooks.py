"""Confirmations from a payment rail, and how this app decides to believe one.

Settling a movement is the moment money becomes real, so the only question that
matters here is *who is allowed to say it happened*. Not the account: a customer
who could confirm their own deposit would be running a mint. Not an unauthenticated
caller who knows an entry id, either -- an id is not a secret, it appears in the
customer's own API responses, and an endpoint that accepted one would be the same
mint with an extra step.

So a confirmation is believed when it is **signed with a secret only the rail and
this deployment hold**, and the signature covers a timestamp as well as the body,
so a confirmation captured off the wire cannot be replayed tomorrow.

    X-Wallet-Timestamp: 1757764800
    X-Wallet-Signature: 8f4c...           (hex HMAC-SHA256)

    signature = HMAC-SHA256(secret, f"{timestamp}.{raw body}")

The secret comes from the environment, per method::

    DJANGO_WALLET_WEBHOOK_SECRETS=stripe-card:whsec_...,coinbase:...

Per method rather than one for the deployment, because the secrets belong to
different companies and a processor that leaks one should not be able to confirm
movements on another's rail. From the environment rather than a column, because a
secret in the database is a secret in every backup and on an admin screen.

A method with no secret configured confirms nothing. That is deliberate and it is
the safe direction: a deployment that wires up a rail and forgets its secret gets
movements that stay pending and a queue somebody notices, rather than an open
door.
"""

import hashlib
import hmac
import time
from typing import Any

from django.conf import settings

from apps.wallet.errors import SignatureInvalid

#: The headers a rail sends them in. Named here so the transport, the tests and
#: the documentation cannot drift apart on the spelling.
TIMESTAMP_HEADER = "X-Wallet-Timestamp"
SIGNATURE_HEADER = "X-Wallet-Signature"

#: How far out of date a confirmation may be. Five minutes is long enough for a
#: slow rail and a clock a little out, and short enough that a captured request
#: is worthless by the time anybody has it.
DEFAULT_TOLERANCE = 300


def secrets() -> dict[str, str]:
    """The configured secret per method code."""
    return dict(getattr(settings, "WALLET_WEBHOOK_SECRETS", {}) or {})


def tolerance() -> int:
    return int(getattr(settings, "WALLET_WEBHOOK_TOLERANCE_SECONDS", DEFAULT_TOLERANCE))


def sign(secret: str, body: bytes, timestamp: int | str) -> str:
    """The signature a rail is expected to send.

    Public because the tests and anybody writing the other half of this need the
    same function, and two implementations of one signature is how a webhook
    ends up rejecting everything on a Friday.
    """
    payload = f"{timestamp}.".encode() + body
    return hmac.new(secret.encode(), payload, hashlib.sha256).hexdigest()


def verify(method_code: str, body: bytes, timestamp: str, signature: str) -> None:
    """Prove this confirmation came from the rail, recently. Silent, or refused.

    Every failure raises the same exception with the same sentence: an endpoint
    that answered "bad signature" for one and "too old" for another would be
    telling whoever is probing it which half they had right.

    The comparison is :func:`hmac.compare_digest` rather than ``==``, because a
    signature check that returns early on the first wrong byte tells an attacker
    how much of it was right -- and unlike most timing attacks, this one is
    against an oracle they may call as often as they like.
    """
    secret = secrets().get(method_code, "")
    if not secret or not signature or not timestamp:
        raise SignatureInvalid("This confirmation could not be verified.")
    try:
        sent_at = int(timestamp)
    except (TypeError, ValueError):
        raise SignatureInvalid("This confirmation could not be verified.") from None
    if abs(int(time.time()) - sent_at) > tolerance():
        raise SignatureInvalid("This confirmation could not be verified.")
    if not hmac.compare_digest(sign(secret, body, timestamp), signature):
        raise SignatureInvalid("This confirmation could not be verified.")


def configured_for(method_code: str) -> bool:
    """Whether this deployment can verify anything at all for one method."""
    return bool(secrets().get(method_code))


def headers_for(secret: str, body: bytes, *, now: Any = None) -> dict[str, str]:
    """The two headers a caller has to send. For tests, and for a rail adapter."""
    stamp = str(int(now if now is not None else time.time()))
    return {TIMESTAMP_HEADER: stamp, SIGNATURE_HEADER: sign(secret, body, stamp)}
