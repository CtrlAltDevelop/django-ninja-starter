"""Turn a proven identity into the credential this project already issues.

The OAuth token modes own the credential models and the signing vocabulary; this
module is the seam that lets a password or one-time-code login mint exactly the
same tokens a social login would, so a project has one revocation story instead
of two.

Every mode but ``none`` hands the client a signed JWT rather than the raw row
handle. The handle lives on as the token's ``jti``, so the database still holds
only a digest and revocation is still a single indexed lookup -- see
:mod:`infrastructure.oauth.core.jwt_tokens` for why both halves are checked.
"""

from datetime import timedelta
from typing import Any

from django.apps import apps
from django.conf import settings
from django.contrib.auth import login as django_login
from django.contrib.auth import logout as django_logout
from django.db import transaction
from django.http import HttpRequest
from django.utils import timezone
from ninja.security import HttpBearer

from infrastructure.common.app_labels import app_installed
from infrastructure.oauth.core import jwt_tokens
from infrastructure.oauth.core.credentials import (
    BEARER,
    TOKEN_MODE_APPS,
    IssuedCredentials,
    bearer_token,
    client_ip,
    credential_handle,
    sign_pair,
    sign_single,
    token_model,
    user_agent,
)
from infrastructure.oauth.core.tokens import hash_token

MODEL_BACKEND = "django.contrib.auth.backends.ModelBackend"

__all__ = [
    "BEARER",
    "TOKEN_MODE_APPS",
    "IssuedCredentials",
    "JwtBearer",
    "api_auth",
    "bearer_token",
    "client_ip",
    "credential_handle",
    "issue_credentials",
    "resolve_request_user",
    "revoke_all_for_user",
    "revoke_credentials",
    "user_agent",
]


def _issue_sliding(request: HttpRequest, user: Any, method: str) -> IssuedCredentials:
    """Issue one sliding credential, whose JWT outlives each idle window.

    The signature is valid for the absolute lifetime, not the idle one: sliding
    means the row's expiry moves forward every time the token is used, and a JWT
    stamped with the *idle* expiry would go stale while the session it stands for
    was still very much alive. The idle timeout is enforced by the row.
    """
    sliding_model = token_model("sliding", "SlidingToken")
    event_model = token_model("sliding", "SlidingTokenEvent")
    now = timezone.now()
    idle = settings.AUTH_SLIDING_IDLE_TIMEOUT_SECONDS
    absolute_expiry = now + timedelta(seconds=settings.AUTH_REFRESH_TOKEN_TTL_SECONDS)
    expiry = min(now + timedelta(seconds=idle), absolute_expiry)
    handle = jwt_tokens.new_handle()
    record = sliding_model.objects.create(
        user=user,
        token_hash=hash_token(handle),
        expires_at=expiry,
        absolute_expires_at=absolute_expiry,
        idle_timeout_seconds=idle,
        issued_ip=client_ip(request),
        user_agent=user_agent(request),
        metadata={"auth_method": method},
    )
    event_model.objects.create(
        token=record,
        event_type="issued",
        new_expires_at=expiry,
        ip_address=client_ip(request),
    )
    return sign_single(
        user,
        mode="sliding",
        session_id=str(record.id),
        methods=[method],
        handle=handle,
        signature_lifetime=absolute_expiry - now,
        expires_in=int((expiry - now).total_seconds()),
    )


def _issue_session(request: HttpRequest, user: Any, method: str) -> IssuedCredentials:
    session_model = token_model("session", "OAuthSession")
    access_model = token_model("session", "SessionAccessToken")
    now = timezone.now()
    session_lifetime = timedelta(seconds=settings.AUTH_REFRESH_TOKEN_TTL_SECONDS)
    access_lifetime = timedelta(seconds=settings.AUTH_ACCESS_TOKEN_TTL_SECONDS)
    session_handle = jwt_tokens.new_handle()
    access_handle = jwt_tokens.new_handle()
    session = session_model.objects.create(
        user=user,
        session_key_hash=hash_token(session_handle),
        expires_at=now + session_lifetime,
        ip_address=client_ip(request),
        user_agent=user_agent(request),
        metadata={"auth_method": method},
    )
    access_model.objects.create(
        user=user,
        session=session,
        token_hash=hash_token(access_handle),
        expires_at=now + access_lifetime,
        issued_ip=client_ip(request),
        user_agent=user_agent(request),
        metadata={"auth_method": method},
    )
    return sign_pair(
        user,
        mode="session",
        session_id=str(session.id),
        methods=[method],
        access_handle=access_handle,
        refresh_handle=session_handle,
        access_lifetime=access_lifetime,
        refresh_lifetime=session_lifetime,
    )


def _issue_rotation(request: HttpRequest, user: Any, method: str) -> IssuedCredentials:
    family_model = token_model("rotation", "TokenFamily")
    refresh_model = token_model("rotation", "RotatingRefreshToken")
    access_model = token_model("rotation", "RotatingAccessToken")
    now = timezone.now()
    refresh_lifetime = timedelta(seconds=settings.AUTH_REFRESH_TOKEN_TTL_SECONDS)
    access_lifetime = timedelta(seconds=settings.AUTH_ACCESS_TOKEN_TTL_SECONDS)
    refresh_handle = jwt_tokens.new_handle()
    access_handle = jwt_tokens.new_handle()
    family = family_model.objects.create(
        user=user,
        expires_at=now + refresh_lifetime,
        issued_ip=client_ip(request),
        user_agent=user_agent(request),
        metadata={"auth_method": method},
    )
    refresh = refresh_model.objects.create(
        user=user,
        family=family,
        token_hash=hash_token(refresh_handle),
        rotation_index=0,
        expires_at=now + refresh_lifetime,
        issued_ip=client_ip(request),
        user_agent=user_agent(request),
        metadata={"auth_method": method},
    )
    access_model.objects.create(
        user=user,
        family=family,
        issued_from=refresh,
        token_hash=hash_token(access_handle),
        expires_at=now + access_lifetime,
        issued_ip=client_ip(request),
        user_agent=user_agent(request),
        metadata={"auth_method": method},
    )
    return sign_pair(
        user,
        mode="rotation",
        session_id=str(family.id),
        methods=[method],
        access_handle=access_handle,
        refresh_handle=refresh_handle,
        access_lifetime=access_lifetime,
        refresh_lifetime=refresh_lifetime,
    )


def issue_credentials(request: HttpRequest, user: Any, *, method: str) -> IssuedCredentials:
    """Mint the credential for a user who has cleared every required factor."""
    mode = settings.AUTH_TOKEN_MODE
    if mode == "none":
        django_login(request, user, backend=MODEL_BACKEND)
        return IssuedCredentials(token_type="session", session_id=request.session.session_key or "")
    issuers = {
        "sliding": _issue_sliding,
        "session": _issue_session,
        "rotation": _issue_rotation,
    }
    with transaction.atomic():
        return issuers[mode](request, user, method)


def _revoke_sliding(digest: str) -> bool:
    sliding_model = token_model("sliding", "SlidingToken")
    event_model = token_model("sliding", "SlidingTokenEvent")
    record = sliding_model.objects.filter(token_hash=digest, revoked_at__isnull=True).first()
    if record is None:
        return False
    record.revoke("logout")
    event_model.objects.create(token=record, event_type="revoked", old_expires_at=record.expires_at)
    return True


def _revoke_session(digest: str) -> bool:
    session_model = token_model("session", "OAuthSession")
    access_model = token_model("session", "SessionAccessToken")
    revocation_model = token_model("session", "SessionRevocation")
    session = session_model.objects.filter(session_key_hash=digest, revoked_at__isnull=True).first()
    if session is None:
        access = (
            access_model.objects.select_related("session")
            .filter(token_hash=digest, revoked_at__isnull=True)
            .first()
        )
        session = access.session if access and access.session.revoked_at is None else None
    if session is None:
        return False
    live_tokens = session.access_tokens.filter(revoked_at__isnull=True).count()
    session.revoke("logout")
    revocation_model.objects.create(
        session=session,
        reason="logout",
        access_tokens_revoked=live_tokens,
    )
    return True


def _revoke_rotation(digest: str) -> bool:
    refresh_model = token_model("rotation", "RotatingRefreshToken")
    access_model = token_model("rotation", "RotatingAccessToken")
    holder = (
        refresh_model.objects.select_related("family").filter(token_hash=digest).first()
        or access_model.objects.select_related("family").filter(token_hash=digest).first()
    )
    if holder is None or holder.family.revoked_at is not None:
        return False
    holder.family.revoke("logout")
    return True


def revoke_credentials(request: HttpRequest, token: str = "") -> bool:
    """Retire the credential this request presented. Returns whether one was found.

    Either half of a pair is accepted. Handing back the refresh token is what a
    client that has let its short-lived access token lapse actually still holds,
    and refusing it would leave the session running.
    """
    mode = settings.AUTH_TOKEN_MODE
    if mode == "none":
        current = getattr(request, "user", None)
        was_authenticated = current is not None and current.is_authenticated
        django_logout(request)
        return bool(was_authenticated)
    presented = token or bearer_token(request)
    if not presented:
        return False
    handle = credential_handle(presented, token_type=jwt_tokens.ACCESS) or credential_handle(
        presented, token_type=jwt_tokens.REFRESH
    )
    if not handle:
        return False
    revokers = {
        "sliding": _revoke_sliding,
        "session": _revoke_session,
        "rotation": _revoke_rotation,
    }
    with transaction.atomic():
        return revokers[mode](hash_token(handle))


def revoke_all_for_user(user: Any, reason: str = "password_changed") -> int:
    """Retire every live credential for an account, after a password change.

    Sweeps all three mode tables rather than only the active one: a project that
    switches modes still has yesterday's tokens sitting in yesterday's table.
    """
    now = timezone.now()
    revoked = 0
    for mode, app_label in TOKEN_MODE_APPS.items():
        if not app_installed(app_label):
            continue
        if mode == "sliding":
            revoked += (
                apps.get_model(app_label, "SlidingToken")
                .objects.filter(user=user, revoked_at__isnull=True)
                .update(revoked_at=now, revocation_reason=reason)
            )
        elif mode == "session":
            sessions = apps.get_model(app_label, "OAuthSession").objects.filter(
                user=user, revoked_at__isnull=True
            )
            for session in sessions:
                session.revoke(reason)
                revoked += 1
        else:
            families = apps.get_model(app_label, "TokenFamily").objects.filter(
                user=user, revoked_at__isnull=True
            )
            for family in families:
                family.revoke(reason)
                revoked += 1
    return revoked


def _user_from_sliding(digest: str) -> Any | None:
    record = (
        token_model("sliding", "SlidingToken")
        .objects.select_related("user")
        .filter(token_hash=digest)
        .first()
    )
    if record is None or not record.is_active:
        return None
    record.slide()
    return record.user


def _user_from_session(digest: str) -> Any | None:
    record = (
        token_model("session", "SessionAccessToken")
        .objects.select_related("user", "session")
        .filter(token_hash=digest)
        .first()
    )
    if record is None or not record.is_active:
        return None
    record.session.last_seen_at = timezone.now()
    record.session.save(update_fields=["last_seen_at"])
    return record.user


def _user_from_rotation(digest: str) -> Any | None:
    record = (
        token_model("rotation", "RotatingAccessToken")
        .objects.select_related("user", "family")
        .filter(token_hash=digest)
        .first()
    )
    return record.user if record is not None and record.is_active else None


def resolve_request_user(request: HttpRequest) -> Any | None:
    """Identify the caller from whatever credential the active mode issues.

    The signature is checked before the database is touched, so a forged or
    expired bearer token costs no query. A token minted under a different token
    mode is refused rather than looked up: its handle belongs to another table,
    where at best it would not be found and at worst would collide.

    Presenting a sliding token counts as activity, so reading it here is what
    keeps an in-use session from idling out.
    """
    mode = settings.AUTH_TOKEN_MODE
    if mode == "none":
        user = getattr(request, "user", None)
        return user if user is not None and user.is_authenticated else None
    presented = bearer_token(request)
    if not presented:
        return None
    try:
        claims = jwt_tokens.decode(presented, token_type=jwt_tokens.ACCESS)
    except jwt_tokens.JwtError:
        return None
    if claims.mode != mode:
        return None
    resolvers = {
        "sliding": _user_from_sliding,
        "session": _user_from_session,
        "rotation": _user_from_rotation,
    }
    user = resolvers[mode](hash_token(claims.handle))
    return user if user is not None and user.is_active else None


class JwtBearer(HttpBearer):
    """The ``auth=`` for endpoints that need a signed-in caller.

    A bearer *class* rather than a bare callable, because that is what puts a
    security scheme into the OpenAPI document. Without it the schema says every
    endpoint is public, Swagger's Authorize button has nothing to fill in, and
    generated clients send no credential at all.

    The ``none`` token mode issues no bearer token, so it is answered from the
    Django session before the header is consulted -- otherwise every protected
    endpoint would refuse a perfectly good session for want of a header it was
    never going to send.
    """

    openapi_name = "JWT"
    openapi_description = "A signed access token, as returned by any login endpoint."

    def __call__(self, request: HttpRequest) -> Any | None:
        if settings.AUTH_TOKEN_MODE == "none":
            return self._adopt(request, resolve_request_user(request))
        return super().__call__(request)

    def authenticate(self, request: HttpRequest, token: str) -> Any | None:
        return self._adopt(request, resolve_request_user(request))

    @staticmethod
    def _adopt(request: HttpRequest, user: Any | None) -> Any | None:
        """Put the resolved account on the request, so a view can read request.user."""
        if user is not None:
            request.user = user
        return user


api_auth = JwtBearer()
