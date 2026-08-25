"""Rotating a refresh token, and what to do when an old one comes back.

Rotation means a refresh token is spendable exactly once: redeeming it mints a
successor and retires the token that was presented. That turns a stolen refresh
token from a permanent foothold into a race, and the loser of that race tells us
a theft happened -- because the only way a spent token can be presented again is
if someone else kept a copy.

When that happens the whole family goes, not just the token. The thief and the
legitimate client are indistinguishable at that point, so the safe move is to
end the session and make both sign in again.
"""

from datetime import timedelta
from typing import Any

from django.conf import settings
from django.db import transaction
from django.http import HttpRequest
from django.utils import timezone

from infrastructure.common.errors import ApiError
from infrastructure.oauth.core import jwt_tokens
from infrastructure.oauth.core.credentials import (
    IssuedCredentials,
    client_ip,
    sign_pair,
    token_model,
    user_agent,
)
from infrastructure.oauth.core.tokens import hash_token

MODE = "rotation"
FINGERPRINT_LENGTH = 16


def _fingerprint(digest: str) -> str:
    """Keep just enough of a digest to correlate reuse reports, never the whole one."""
    return digest[:FINGERPRINT_LENGTH]


def _record_reuse(request: HttpRequest, presented: Any) -> None:
    reuse_model = token_model(MODE, "RefreshTokenReuseEvent")
    reuse_model.objects.create(
        family=presented.family,
        refresh_token=presented,
        presented_fingerprint=_fingerprint(presented.token_hash),
        ip_address=client_ip(request),
        user_agent=user_agent(request),
    )
    presented.mark_reuse()


class _Reused(Exception):
    """Internal signal: the presented token had already been spent.

    Carried out of the locked block rather than reported from inside it, because
    reporting means raising, and an exception thrown mid-transaction would roll
    back the very audit row and family revocation that make the report matter.
    """

    def __init__(self, token: Any) -> None:
        super().__init__("refresh token reuse")
        self.token = token


def rotate(request: HttpRequest, refresh_token: str) -> IssuedCredentials:
    """Spend a refresh token and return its successor pair.

    Read and write happen under one row lock: two clients racing with the same
    token must not both walk away with a valid successor, which is exactly what
    an unlocked read-then-write would allow. The loser of that race is what
    surfaces as reuse.
    """
    try:
        handle = jwt_tokens.decode(refresh_token, token_type=jwt_tokens.REFRESH).handle
    except jwt_tokens.JwtError as error:
        raise ApiError(str(error), status=401) from error
    refresh_model = token_model(MODE, "RotatingRefreshToken")
    access_model = token_model(MODE, "RotatingAccessToken")

    try:
        with transaction.atomic():
            presented = (
                refresh_model.objects.select_for_update()
                .select_related("family", "user")
                .filter(token_hash=hash_token(handle))
                .first()
            )
            if presented is None:
                raise ApiError("That refresh token is not valid.", status=401)
            if presented.used_at is not None:
                raise _Reused(presented)
            if presented.family.revoked_at is not None:
                raise ApiError("This session has ended. Sign in again.", status=401)
            if not presented.is_active:
                raise ApiError("That refresh token has expired.", status=401)

            user = presented.user
            family = presented.family
            now = timezone.now()
            access_lifetime = timedelta(seconds=settings.AUTH_ACCESS_TOKEN_TTL_SECONDS)
            access_handle = jwt_tokens.new_handle()
            refresh_handle = jwt_tokens.new_handle()
            method = str(presented.metadata.get("auth_method", ""))

            successor = refresh_model.objects.create(
                user=user,
                family=family,
                parent=presented,
                token_hash=hash_token(refresh_handle),
                rotation_index=presented.rotation_index + 1,
                expires_at=family.expires_at,
                issued_ip=client_ip(request),
                user_agent=user_agent(request),
                metadata={"auth_method": method},
            )
            presented.mark_used(successor)
            access_model.objects.create(
                user=user,
                family=family,
                issued_from=successor,
                token_hash=hash_token(access_handle),
                expires_at=now + access_lifetime,
                issued_ip=client_ip(request),
                user_agent=user_agent(request),
                metadata={"auth_method": method},
            )

            return sign_pair(
                user,
                mode=MODE,
                session_id=str(family.id),
                methods=[method] if method else [],
                access_handle=access_handle,
                refresh_handle=refresh_handle,
                access_lifetime=access_lifetime,
                refresh_lifetime=family.expires_at - now,
            )
    except _Reused as reuse:
        _record_reuse(request, reuse.token)
        raise ApiError("This session has been ended for your security.", status=401) from reuse


def live_families(user: Any) -> Any:
    """Return the account's families that have neither expired nor been revoked."""
    family_model = token_model(MODE, "TokenFamily")
    return (
        family_model.objects.filter(
            user=user,
            revoked_at__isnull=True,
            reuse_detected_at__isnull=True,
            expires_at__gt=timezone.now(),
        )
        .order_by("-created_at")
        .prefetch_related("refresh_tokens")
    )


def revoke_family(user: Any, family_id: str, reason: str = "logout") -> bool:
    """End one of the account's own sessions. Returns whether one was found."""
    family_model = token_model(MODE, "TokenFamily")
    family = family_model.objects.filter(pk=family_id, user=user, revoked_at__isnull=True).first()
    if family is None:
        return False
    family.revoke(reason)
    return True
