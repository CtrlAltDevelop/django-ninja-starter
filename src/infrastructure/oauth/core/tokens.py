"""Generate opaque credentials and irreversible lookup hashes."""

import hashlib
import secrets


def generate_token(byte_length: int = 48) -> str:
    """Return a URL-safe credential with enough entropy for bearer-token use."""
    return secrets.token_urlsafe(byte_length)


def hash_token(token: str) -> str:
    """Return the SHA-256 lookup digest for a secret token."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()
