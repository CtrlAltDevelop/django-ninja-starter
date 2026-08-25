"""Refreshing inside a server-side session.

The session key is the long-lived half and is *not* rotated: this mode's whole
point is that one session can mint many short access tokens and revoke them
independently, which is what lets an account list its devices and end one. So
refreshing issues a new access token against the same session and hands the
session key straight back.
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

MODE = "session"


def refresh_access(request: HttpRequest, session_token: str) -> IssuedCredentials:
    """Mint a new access token for the session this key names."""
    try:
        handle = jwt_tokens.decode(session_token, token_type=jwt_tokens.REFRESH).handle
    except jwt_tokens.JwtError as error:
        raise ApiError(str(error), status=401) from error
    session_model = token_model(MODE, "OAuthSession")
    access_model = token_model(MODE, "SessionAccessToken")

    with transaction.atomic():
        session = (
            session_model.objects.select_for_update()
            .select_related("user")
            .filter(session_key_hash=hash_token(handle))
            .first()
        )
        if session is None:
            raise ApiError("That session key is not valid.", status=401)
        if not session.is_active:
            raise ApiError("This session has ended. Sign in again.", status=401)

        now = timezone.now()
        access_lifetime = timedelta(seconds=settings.AUTH_ACCESS_TOKEN_TTL_SECONDS)
        access_handle = jwt_tokens.new_handle()
        method = str(session.metadata.get("auth_method", ""))
        access_model.objects.create(
            user=session.user,
            session=session,
            token_hash=hash_token(access_handle),
            expires_at=now + access_lifetime,
            issued_ip=client_ip(request),
            user_agent=user_agent(request),
            metadata={"auth_method": method},
        )
        session.last_seen_at = now
        session.save(update_fields=["last_seen_at"])

        return sign_pair(
            session.user,
            mode=MODE,
            session_id=str(session.id),
            methods=[method] if method else [],
            access_handle=access_handle,
            refresh_handle=handle,
            access_lifetime=access_lifetime,
            refresh_lifetime=session.expires_at - now,
        )


def live_sessions(user: Any) -> Any:
    """Return the account's sessions that have neither expired nor been revoked."""
    session_model = token_model(MODE, "OAuthSession")
    return session_model.objects.filter(
        user=user, revoked_at__isnull=True, expires_at__gt=timezone.now()
    ).order_by("-created_at")


def revoke_session(user: Any, session_id: str, reason: str = "logout") -> bool:
    """End one of the account's own sessions. Returns whether one was found."""
    session_model = token_model(MODE, "OAuthSession")
    revocation_model = token_model(MODE, "SessionRevocation")
    session = session_model.objects.filter(
        pk=session_id, user=user, revoked_at__isnull=True
    ).first()
    if session is None:
        return False
    live_tokens = session.access_tokens.filter(revoked_at__isnull=True).count()
    session.revoke(reason)
    revocation_model.objects.create(
        session=session,
        revoked_by=user,
        reason=reason,
        access_tokens_revoked=live_tokens,
    )
    return True
