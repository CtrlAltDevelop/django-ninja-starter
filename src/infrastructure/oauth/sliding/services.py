"""Refreshing a sliding token, which has no second token to refresh with.

A sliding token extends itself every time it is used, so "refresh" here does not
mean exchanging one credential for another -- it means asking the server to push
the idle window out now and say how long is left. That matters to a client that
has been idle and wants to know whether it still has a session before it lets a
user start typing.

The token value is returned unchanged, because its signature already covers the
absolute lifetime. Only the idle deadline moves.
"""

from typing import Any

from django.http import HttpRequest
from django.utils import timezone

from infrastructure.common.errors import ApiError
from infrastructure.oauth.core import jwt_tokens
from infrastructure.oauth.core.credentials import (
    IssuedCredentials,
    bearer_token,
    sign_single,
    token_model,
)
from infrastructure.oauth.core.tokens import hash_token

MODE = "sliding"


def slide(request: HttpRequest, presented: str = "") -> IssuedCredentials:
    """Push the idle deadline out and report the token's remaining life."""
    token = presented or bearer_token(request)
    try:
        claims = jwt_tokens.decode(token, token_type=jwt_tokens.ACCESS)
    except jwt_tokens.JwtError as error:
        raise ApiError(str(error), status=401) from error

    sliding_model = token_model(MODE, "SlidingToken")
    event_model = token_model(MODE, "SlidingTokenEvent")
    record = (
        sliding_model.objects.select_related("user")
        .filter(token_hash=hash_token(claims.handle))
        .first()
    )
    if record is None:
        raise ApiError("That token is not valid.", status=401)
    previous = record.expires_at
    if not record.slide():
        raise ApiError("This session has ended. Sign in again.", status=401)
    event_model.objects.create(
        token=record,
        event_type="slid",
        old_expires_at=previous,
        new_expires_at=record.expires_at,
    )
    return sign_single(
        record.user,
        mode=MODE,
        session_id=str(record.id),
        methods=[str(record.metadata.get("auth_method", ""))],
        handle=claims.handle,
        signature_lifetime=record.absolute_expires_at - timezone.now(),
        expires_in=int((record.expires_at - timezone.now()).total_seconds()),
    )


def live_tokens(user: Any) -> Any:
    """Return the account's tokens that have neither expired nor been revoked."""
    now = timezone.now()
    sliding_model = token_model(MODE, "SlidingToken")
    return sliding_model.objects.filter(
        user=user,
        revoked_at__isnull=True,
        expires_at__gt=now,
        absolute_expires_at__gt=now,
    ).order_by("-issued_at")


def revoke_token(user: Any, token_id: str, reason: str = "logout") -> bool:
    """Revoke one of the account's own tokens. Returns whether one was found."""
    sliding_model = token_model(MODE, "SlidingToken")
    event_model = token_model(MODE, "SlidingTokenEvent")
    record = sliding_model.objects.filter(pk=token_id, user=user, revoked_at__isnull=True).first()
    if record is None:
        return False
    record.revoke(reason)
    event_model.objects.create(token=record, event_type="revoked", old_expires_at=record.expires_at)
    return True
