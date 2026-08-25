"""Turn a proven identity into whatever credential the project already issues.

The OAuth token modes own the credential models; this module is the seam that
lets a password or one-time-code login mint exactly the same tokens a social
login would, so a project has one revocation story instead of two. Models are
resolved through the app registry, never imported, so enabling ``sliding`` does
not drag ``rotation``'s tables into the schema.

Every mode but ``none`` hands the client a signed JWT rather than the raw row
handle. The handle lives on as the token's ``jti``, so the database still holds
only a digest and revocation is still a single indexed lookup -- see
:mod:`infrastructure.oauth.core.jwt_tokens` for why both halves are checked.
"""

from dataclasses import dataclass
from datetime import timedelta
from typing import Any

from django.apps import apps
from django.conf import settings
from django.contrib.auth import login as django_login
from django.contrib.auth import logout as django_logout
from django.core.exceptions import ImproperlyConfigured
from django.db import transaction
from django.http import HttpRequest
from django.utils import timezone

from infrastructure.common.app_labels import app_installed
from infrastructure.oauth.core import jwt_tokens
from infrastructure.oauth.core.tokens import hash_token

MODEL_BACKEND = "django.contrib.auth.backends.ModelBackend"
TOKEN_MODE_APPS = {
    "sliding": "oauth_sliding",
    "session": "oauth_session",
    "rotation": "oauth_rotation",
}


@dataclass(frozen=True)
class IssuedCredentials:
    """What a successful login hands back to the client."""

    token_type: str
    access_token: str = ""
    refresh_token: str = ""
    expires_in: int | None = None
    session_id: str = ""


def client_ip(request: HttpRequest) -> str | None:
    forwarded = request.META.get("HTTP_X_FORWARDED_FOR", "")
    return (
        forwarded.split(",", 1)[0].strip() if forwarded else request.META.get("REMOTE_ADDR")
    ) or None


def user_agent(request: HttpRequest) -> str:
    return request.META.get("HTTP_USER_AGENT", "")


def bearer_token(request: HttpRequest) -> str:
    header = request.META.get("HTTP_AUTHORIZATION", "")
    scheme, _, value = header.partition(" ")
    return value.strip() if scheme.lower() == "bearer" else ""


def _model(mode: str, name: str) -> Any:
    app_label = TOKEN_MODE_APPS[mode]
    if not app_installed(app_label):
        raise ImproperlyConfigured(
            f"DJANGO_AUTH_TOKEN_MODE={mode} needs DJANGO_OAUTH_MODE to enable {app_label}."
        )
    return apps.get_model(app_label, name)


def _pair(
    user: Any,
    *,
    mode: str,
    session_id: str,
    method: str,
    access_handle: str,
    refresh_handle: str,
    access_lifetime: timedelta,
    refresh_lifetime: timedelta,
) -> IssuedCredentials:
    """Wrap a freshly stored access/refresh handle pair as signed tokens."""
    subject = str(user.pk)
    access_token, _ = jwt_tokens.mint(
        subject=subject,
        handle=access_handle,
        token_type=jwt_tokens.ACCESS,
        lifetime=access_lifetime,
        mode=mode,
        session_id=session_id,
        methods=[method],
    )
    refresh_token, _ = jwt_tokens.mint(
        subject=subject,
        handle=refresh_handle,
        token_type=jwt_tokens.REFRESH,
        lifetime=refresh_lifetime,
        mode=mode,
        session_id=session_id,
        methods=[method],
    )
    return IssuedCredentials(
        token_type="bearer",
        access_token=access_token,
        refresh_token=refresh_token,
        expires_in=int(access_lifetime.total_seconds()),
        session_id=session_id,
    )


def _issue_sliding(request: HttpRequest, user: Any, method: str) -> IssuedCredentials:
    """Issue one sliding credential, whose JWT outlives each idle window.

    The signature is valid for the absolute lifetime, not the idle one: sliding
    means the row's expiry moves forward every time the token is used, and a JWT
    stamped with the *idle* expiry would go stale while the session it stands for
    was still very much alive. The idle timeout is enforced by the row.
    """
    token_model = _model("sliding", "SlidingToken")
    event_model = _model("sliding", "SlidingTokenEvent")
    now = timezone.now()
    idle = settings.AUTH_SLIDING_IDLE_TIMEOUT_SECONDS
    absolute_expiry = now + timedelta(seconds=settings.AUTH_REFRESH_TOKEN_TTL_SECONDS)
    expiry = min(now + timedelta(seconds=idle), absolute_expiry)
    handle = jwt_tokens.new_handle()
    record = token_model.objects.create(
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
    access_token, _ = jwt_tokens.mint(
        subject=str(user.pk),
        handle=handle,
        token_type=jwt_tokens.ACCESS,
        lifetime=absolute_expiry - now,
        mode="sliding",
        session_id=str(record.id),
        methods=[method],
    )
    return IssuedCredentials(
        token_type="bearer",
        access_token=access_token,
        expires_in=int((expiry - now).total_seconds()),
        session_id=str(record.id),
    )


def _issue_session(request: HttpRequest, user: Any, method: str) -> IssuedCredentials:
    session_model = _model("session", "OAuthSession")
    access_model = _model("session", "SessionAccessToken")
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
    return _pair(
        user,
        mode="session",
        session_id=str(session.id),
        method=method,
        access_handle=access_handle,
        refresh_handle=session_handle,
        access_lifetime=access_lifetime,
        refresh_lifetime=session_lifetime,
    )


def _issue_rotation(request: HttpRequest, user: Any, method: str) -> IssuedCredentials:
    family_model = _model("rotation", "TokenFamily")
    refresh_model = _model("rotation", "RotatingRefreshToken")
    access_model = _model("rotation", "RotatingAccessToken")
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
    return _pair(
        user,
        mode="rotation",
        session_id=str(family.id),
        method=method,
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
    token_model = _model("sliding", "SlidingToken")
    event_model = _model("sliding", "SlidingTokenEvent")
    record = token_model.objects.filter(token_hash=digest, revoked_at__isnull=True).first()
    if record is None:
        return False
    record.revoke("logout")
    event_model.objects.create(token=record, event_type="revoked", old_expires_at=record.expires_at)
    return True


def _revoke_session(digest: str) -> bool:
    session_model = _model("session", "OAuthSession")
    access_model = _model("session", "SessionAccessToken")
    revocation_model = _model("session", "SessionRevocation")
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
    refresh_model = _model("rotation", "RotatingRefreshToken")
    access_model = _model("rotation", "RotatingAccessToken")
    holder = (
        refresh_model.objects.select_related("family").filter(token_hash=digest).first()
        or access_model.objects.select_related("family").filter(token_hash=digest).first()
    )
    if holder is None or holder.family.revoked_at is not None:
        return False
    holder.family.revoke("logout")
    return True


def credential_handle(token: str, *, token_type: str) -> str:
    """Return the row handle inside a token, or an empty string if it is not ours.

    Signing out is deliberately forgiving: a client that presents a token which
    has already expired, or which was signed under a rotated key, has nothing
    left to revoke and should not be told that its logout failed.
    """
    try:
        return jwt_tokens.decode(token, token_type=token_type).handle
    except jwt_tokens.JwtError:
        return ""


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
        _model("sliding", "SlidingToken")
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
        _model("session", "SessionAccessToken")
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
        _model("rotation", "RotatingAccessToken")
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


def api_auth(request: HttpRequest) -> Any | None:
    """Django Ninja ``auth=`` callable for endpoints that need a signed-in user."""
    user = resolve_request_user(request)
    if user is not None:
        request.user = user
    return user
