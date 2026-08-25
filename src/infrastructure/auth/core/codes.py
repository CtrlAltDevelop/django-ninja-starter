"""Generate and irreversibly fingerprint the short codes sent to users."""

import hmac
import secrets
from hashlib import sha256

from django.conf import settings


def generate_numeric_code(digits: int | None = None) -> str:
    """Return a uniformly random decimal code, leading zeros preserved."""
    length = digits if digits is not None else settings.AUTH_CODE_DIGITS
    if length < 4:
        raise ValueError("codes must be at least 4 digits")
    upper = 10**length
    return str(secrets.randbelow(upper)).zfill(length)


def generate_ticket() -> str:
    """Return the opaque handle a client trades back in for its challenge."""
    return secrets.token_urlsafe(32)


def hash_code(ticket: str, purpose: str, code: str) -> str:
    """Fingerprint a code so a leaked store cannot be replayed.

    A six-digit code has far too little entropy to survive a bare digest, so the
    ticket and purpose are bound in as salt and the whole thing is keyed with
    ``SECRET_KEY``. Recovering a code then requires the key as well as the dump.
    """
    message = f"{purpose}:{ticket}:{code}".encode()
    return hmac.new(settings.SECRET_KEY.encode(), message, sha256).hexdigest()


def codes_match(expected_hash: str, ticket: str, purpose: str, code: str) -> bool:
    """Compare a presented code against a stored fingerprint in constant time.

    Codeless challenges store an empty fingerprint; treating that as "matches
    the empty code" would let a bare ticket satisfy a verification step, so both
    empty operands are rejected outright.
    """
    if not expected_hash or not code:
        return False
    return hmac.compare_digest(expected_hash, hash_code(ticket, purpose, code))


def ticket_key(ticket: str) -> str:
    """Return the storage key for a ticket without storing the ticket itself."""
    return sha256(ticket.encode()).hexdigest()
