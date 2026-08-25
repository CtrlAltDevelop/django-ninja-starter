"""Sign and verify the JSON Web Tokens the API accepts as credentials.

Every login method ends here: whatever proved the user's identity, the thing
handed back is a signed JWT. That gives a resource server enough to reject a
forged or expired token without a database round trip.

Revocation still has to be immediate, though, and a self-contained token cannot
be taken back. So the JWT carries a ``jti`` that is the *only* secret in it --
48 random bytes -- and the credential row in the database is keyed by that
handle's digest. Verification is therefore two steps that answer two different
questions: the signature says "this token was issued by us and has not expired",
and the row says "and it has not since been revoked". Neither is sufficient
alone, and the second is what makes logout, rotation and reuse detection real.

Nothing here reads a model. The token-mode apps own the rows; this module only
knows how to wrap and unwrap a handle.
"""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Final

import jwt
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.utils import timezone

from infrastructure.oauth.core.tokens import generate_token

ACCESS: Final = "access"
REFRESH: Final = "refresh"

SYMMETRIC_ALGORITHMS: Final = frozenset({"HS256", "HS384", "HS512"})
ASYMMETRIC_ALGORITHMS: Final = frozenset({"RS256", "RS384", "RS512", "ES256", "ES384", "ES512"})
SUPPORTED_ALGORITHMS: Final = SYMMETRIC_ALGORITHMS | ASYMMETRIC_ALGORITHMS

REQUIRED_CLAIMS: Final = ["iss", "sub", "exp", "iat", "jti"]


class JwtError(ValueError):
    """Raised when a token is malformed, unsigned by us, expired, or the wrong type."""


@dataclass(frozen=True)
class TokenClaims:
    """The parts of a verified token the rest of the code acts on."""

    handle: str
    """The ``jti``: the random secret whose digest keys the credential row."""

    subject: str
    token_type: str
    mode: str
    session_id: str
    methods: list[str]
    expires_at: datetime


def _algorithm() -> str:
    algorithm = str(settings.AUTH_JWT_ALGORITHM)
    if algorithm not in SUPPORTED_ALGORITHMS:
        raise ImproperlyConfigured(
            f"DJANGO_AUTH_JWT_ALGORITHM must be one of: {', '.join(sorted(SUPPORTED_ALGORITHMS))}"
        )
    return algorithm


def signing_key() -> str:
    """Return the key used to sign, falling back to ``SECRET_KEY`` only for HMAC.

    An asymmetric algorithm has no sensible fallback -- there is no private key
    to derive from a shared secret -- so a missing one is a configuration error
    rather than something to paper over with a weaker default.
    """
    configured = str(settings.AUTH_JWT_SIGNING_KEY)
    if configured:
        return configured
    if _algorithm() in SYMMETRIC_ALGORITHMS:
        return str(settings.SECRET_KEY)
    raise ImproperlyConfigured(
        f"DJANGO_AUTH_JWT_SIGNING_KEY is required for {_algorithm()}: "
        "supply the PEM-encoded private key."
    )


def verifying_key() -> str:
    """Return the key used to verify. For HMAC this is the signing key itself."""
    if _algorithm() in SYMMETRIC_ALGORITHMS:
        return signing_key()
    configured = str(settings.AUTH_JWT_VERIFYING_KEY)
    if configured:
        return configured
    raise ImproperlyConfigured(
        f"DJANGO_AUTH_JWT_VERIFYING_KEY is required for {_algorithm()}: "
        "supply the PEM-encoded public key."
    )


def new_handle() -> str:
    """Mint the random handle that becomes a token's ``jti``."""
    return generate_token()


def mint(
    *,
    subject: str,
    handle: str,
    token_type: str,
    lifetime: timedelta,
    mode: str,
    session_id: str = "",
    methods: list[str] | None = None,
    extra_claims: dict[str, Any] | None = None,
) -> tuple[str, datetime]:
    """Return a signed token for ``handle`` and the moment it expires."""
    issued_at = timezone.now()
    expires_at = issued_at + lifetime
    claims: dict[str, Any] = {
        "iss": str(settings.AUTH_JWT_ISSUER),
        "sub": subject,
        "iat": issued_at,
        "nbf": issued_at,
        "exp": expires_at,
        "jti": handle,
        "typ": token_type,
        "mode": mode,
        **(extra_claims or {}),
    }
    if session_id:
        claims["sid"] = session_id
    if methods:
        claims["amr"] = methods
    audience = str(settings.AUTH_JWT_AUDIENCE)
    if audience:
        claims["aud"] = audience
    return jwt.encode(claims, signing_key(), algorithm=_algorithm()), expires_at


def decode(token: str, *, token_type: str) -> TokenClaims:
    """Verify a token's signature, timing and type, or explain why it failed.

    The ``typ`` check is what stops a refresh token from being presented as a
    bearer credential, and an access token from being spent at the refresh
    endpoint -- both would otherwise verify perfectly well.
    """
    if not token:
        raise JwtError("No credential was presented.")
    audience = str(settings.AUTH_JWT_AUDIENCE)
    try:
        claims = jwt.decode(
            token,
            verifying_key(),
            algorithms=[_algorithm()],
            issuer=str(settings.AUTH_JWT_ISSUER),
            audience=audience or None,
            leeway=settings.AUTH_JWT_LEEWAY_SECONDS,
            options={
                "verify_aud": bool(audience),
                "require": REQUIRED_CLAIMS,
            },
        )
    except jwt.ExpiredSignatureError as error:
        raise JwtError("This credential has expired.") from error
    except jwt.PyJWTError as error:
        raise JwtError("This credential is not valid.") from error

    if str(claims.get("typ", "")) != token_type:
        raise JwtError(f"Expected a {token_type} token.")
    methods = claims.get("amr") or []
    return TokenClaims(
        handle=str(claims["jti"]),
        subject=str(claims["sub"]),
        token_type=token_type,
        mode=str(claims.get("mode", "")),
        session_id=str(claims.get("sid", "")),
        methods=[str(item) for item in methods] if isinstance(methods, list) else [],
        expires_at=datetime.fromtimestamp(int(claims["exp"]), tz=UTC),
    )
