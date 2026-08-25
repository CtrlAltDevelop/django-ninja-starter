"""Shared, provider-neutral social OAuth authorization-code orchestration."""

import base64
import hashlib
import secrets
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any, Protocol

from django.apps import apps
from django.conf import settings
from django.contrib.auth import get_user_model, login
from django.db import transaction
from django.http import HttpRequest, HttpResponse, HttpResponseRedirect, JsonResponse
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme
from django.utils.module_loading import import_string

from infrastructure.oauth_core.crypto import decrypt_secret, encrypt_secret
from infrastructure.oauth_core.models import AbstractSocialAccount, SocialLoginAttempt
from infrastructure.oauth_core.tokens import hash_token


class OAuthProviderError(RuntimeError):
    pass


@dataclass(frozen=True)
class ProviderTokens:
    access_token: str
    refresh_token: str = ""
    expires_in: int | None = None
    scopes: list[str] = field(default_factory=list)
    id_token: str = ""


@dataclass(frozen=True)
class SocialProfile:
    subject: str
    email: str = ""
    email_verified: bool = False
    display_name: str = ""
    avatar_url: str = ""
    claims: dict[str, Any] = field(default_factory=dict)


class SocialProvider(Protocol):
    key: str
    account_model: str
    uses_nonce: bool
    uses_pkce: bool
    uses_form_post: bool

    def is_configured(self) -> bool: ...

    def authorization_url(
        self,
        *,
        state: str,
        nonce: str,
        code_challenge: str,
        redirect_uri: str,
    ) -> str: ...

    def complete(
        self,
        *,
        code: str,
        redirect_uri: str,
        code_verifier: str,
        nonce_hash: str,
        callback_data: dict[str, str],
    ) -> tuple[ProviderTokens, SocialProfile]: ...


def _client_ip(request: HttpRequest) -> str | None:
    forwarded = request.META.get("HTTP_X_FORWARDED_FOR", "")
    return (
        forwarded.split(",", 1)[0].strip() if forwarded else request.META.get("REMOTE_ADDR")
    ) or None


def _binding_cookie_name(provider: SocialProvider) -> str:
    return f"oauth_binding_{provider.key}"


def _write_binding_cookie(
    response: HttpResponse,
    request: HttpRequest,
    provider: SocialProvider,
    binding: str,
    max_age: int,
) -> None:
    """Tie the pending attempt to the browser that started it.

    Providers that answer with a cross-site ``form_post`` need ``SameSite=None``,
    which browsers only honour on a ``Secure`` cookie; those providers are
    required to use HTTPS callbacks anyway. Expiring the cookie has to repeat the
    same attributes, otherwise the browser keeps the original.
    """
    cross_site = provider.uses_form_post
    response.set_cookie(
        _binding_cookie_name(provider),
        binding,
        max_age=max_age,
        httponly=True,
        secure=True if cross_site else request.is_secure(),
        samesite="None" if cross_site else "Lax",
    )


def _verify_binding(
    request: HttpRequest,
    provider: SocialProvider,
    attempt: SocialLoginAttempt,
) -> None:
    if not attempt.binding_hash:
        return
    presented = request.COOKIES.get(_binding_cookie_name(provider), "")
    if not presented or not secrets.compare_digest(hash_token(presented), attempt.binding_hash):
        raise OAuthProviderError("OAuth state did not originate in this browser")


def _callback_uri(request: HttpRequest, provider: SocialProvider) -> str:
    configured = settings.OAUTH_PROVIDER_CONFIG[provider.key].get("redirect_uri", "")
    if configured:
        return str(configured)
    start_path = request.path.rstrip("/")
    callback_path = f"{start_path.removesuffix('/start')}/callback"
    return request.build_absolute_uri(callback_path)


def _safe_next_url(request: HttpRequest) -> str:
    candidate = request.GET.get("next") or settings.OAUTH_LOGIN_REDIRECT_URL
    if url_has_allowed_host_and_scheme(
        candidate,
        allowed_hosts={request.get_host()},
        require_https=request.is_secure(),
    ):
        return candidate
    return "/"


def begin_social_login(request: HttpRequest, provider: SocialProvider) -> HttpResponse:
    if not provider.is_configured():
        return JsonResponse({"detail": f"{provider.key} OAuth is not configured"}, status=503)

    state = secrets.token_urlsafe(48)
    nonce = secrets.token_urlsafe(48) if provider.uses_nonce else ""
    verifier = secrets.token_urlsafe(64) if provider.uses_pkce else ""
    challenge = (
        base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
        if verifier
        else ""
    )
    binding = secrets.token_urlsafe(32)
    redirect_uri = _callback_uri(request, provider)
    user = request.user if request.user.is_authenticated else None
    scopes = list(settings.OAUTH_PROVIDER_CONFIG[provider.key]["scopes"])
    SocialLoginAttempt.objects.create(
        provider=provider.key,
        state_hash=hash_token(state),
        binding_hash=hash_token(binding),
        nonce_hash=hash_token(nonce) if nonce else "",
        code_verifier_encrypted=encrypt_secret(verifier),
        user=user,
        redirect_uri=redirect_uri,
        next_url=_safe_next_url(request),
        requested_scopes=scopes,
        expires_at=timezone.now() + timedelta(seconds=settings.OAUTH_STATE_TTL_SECONDS),
        ip_address=_client_ip(request),
        user_agent=request.META.get("HTTP_USER_AGENT", ""),
    )
    response = HttpResponseRedirect(
        provider.authorization_url(
            state=state,
            nonce=nonce,
            code_challenge=challenge,
            redirect_uri=redirect_uri,
        )
    )
    _write_binding_cookie(response, request, provider, binding, settings.OAUTH_STATE_TTL_SECONDS)
    return response


def _consume_attempt(provider: SocialProvider, state: str) -> SocialLoginAttempt:
    with transaction.atomic():
        try:
            attempt = SocialLoginAttempt.objects.select_for_update().get(
                provider=provider.key,
                state_hash=hash_token(state),
            )
        except SocialLoginAttempt.DoesNotExist as error:
            raise OAuthProviderError("Invalid OAuth state") from error
        if not attempt.is_active:
            raise OAuthProviderError("OAuth state has expired or was already used")
        attempt.consumed_at = timezone.now()
        attempt.save(update_fields=["consumed_at"])
    return attempt


def _create_user(profile: SocialProfile, provider_key: str) -> Any:
    user_model = get_user_model()
    username_field = user_model.USERNAME_FIELD
    field = user_model._meta.get_field(username_field)
    subject_fingerprint = hashlib.sha256(profile.subject.encode()).hexdigest()[:16]
    random_suffix = secrets.token_hex(4)
    if username_field == "email":
        identifier = f"{provider_key}+{subject_fingerprint}.{random_suffix}@oauth.invalid"
    else:
        identifier = f"{provider_key}_{subject_fingerprint}_{random_suffix}"
    max_length = getattr(field, "max_length", None)
    if max_length:
        identifier = identifier[:max_length]
    attributes = {username_field: identifier}
    if profile.email and username_field != "email" and hasattr(user_model, "email"):
        email_field = user_model._meta.get_field("email")
        attributes["email"] = (
            f"{provider_key}+{subject_fingerprint}.{random_suffix}@oauth.invalid"
            if email_field.unique
            else profile.email
        )
    return user_model._default_manager.create_user(**attributes)


def _fitted(account: AbstractSocialAccount, field_name: str, value: str) -> str:
    """Clip a provider-supplied value to the width its column actually allows."""
    max_length = account._meta.get_field(field_name).max_length
    return value[:max_length] if max_length else value


def _resolve_account(
    provider: SocialProvider,
    attempt: SocialLoginAttempt,
    profile: SocialProfile,
    tokens: ProviderTokens,
) -> AbstractSocialAccount:
    if not profile.subject:
        raise OAuthProviderError(f"{provider.key} did not return a subject identifier")
    account_model = apps.get_model(provider.account_model)
    account = account_model.objects.select_related("user").filter(subject=profile.subject).first()
    if account and attempt.user_id and account.user_id != attempt.user_id:
        raise OAuthProviderError("This provider account is linked to another user")

    if account is None:
        created_local_user = False
        if attempt.user_id:
            user = attempt.user
        else:
            resolver_path = settings.OAUTH_USER_RESOLVER
            user = import_string(resolver_path)(provider.key, profile) if resolver_path else None
            if user is None and settings.OAUTH_AUTO_CREATE_USERS:
                user = _create_user(profile, provider.key)
                created_local_user = True
            if user is None:
                raise OAuthProviderError("No local user is linked to this provider account")
        if getattr(user, "pk", None) is None:
            raise OAuthProviderError("OAuth user resolver must return a saved user")
        account, account_created = account_model.objects.get_or_create(
            subject=profile.subject,
            defaults={"user": user},
        )
        if not account_created and account.user_id != user.pk:
            if created_local_user:
                user.delete()
            else:
                raise OAuthProviderError("This provider account is linked to another user")

    account.email = _fitted(account, "email", profile.email)
    account.email_verified = profile.email_verified
    account.display_name = _fitted(account, "display_name", profile.display_name)
    account.avatar_url = _fitted(account, "avatar_url", profile.avatar_url)
    account.scopes = tokens.scopes
    account.raw_claims = profile.claims
    account.last_login_at = timezone.now()
    if hasattr(account, "hosted_domain"):
        account.hosted_domain = str(profile.claims.get("hd", ""))
    if hasattr(account, "tenant_id"):
        account.tenant_id = str(profile.claims.get("tid", ""))
    if hasattr(account, "login"):
        account.login = str(profile.claims.get("login", ""))
    if hasattr(account, "is_private_email"):
        account.is_private_email = (
            str(profile.claims.get("is_private_email", "false")).lower() == "true"
        )
    if hasattr(account, "real_user_status"):
        account.real_user_status = profile.claims.get("real_user_status")
    if tokens.expires_in is not None:
        account.token_expires_at = timezone.now() + timedelta(seconds=tokens.expires_in)
    if settings.OAUTH_STORE_PROVIDER_TOKENS:
        account.access_token_encrypted = encrypt_secret(tokens.access_token)
        if tokens.refresh_token:
            account.refresh_token_encrypted = encrypt_secret(tokens.refresh_token)
    else:
        account.access_token_encrypted = ""
        account.refresh_token_encrypted = ""
    account.save()
    return account


def finish_social_login(
    request: HttpRequest,
    provider: SocialProvider,
    callback_data: dict[str, str],
) -> HttpResponse:
    state = callback_data.get("state", "")
    if not state:
        return JsonResponse({"detail": "Missing OAuth state"}, status=400)
    attempt: SocialLoginAttempt | None = None
    try:
        attempt = _consume_attempt(provider, state)
        _verify_binding(request, provider, attempt)
        if callback_data.get("error"):
            raise OAuthProviderError(
                callback_data.get("error_description") or callback_data["error"]
            )
        code = callback_data.get("code", "")
        if not code:
            raise OAuthProviderError("Missing authorization code")
        try:
            code_verifier = decrypt_secret(attempt.code_verifier_encrypted)
        except ValueError as error:
            raise OAuthProviderError("OAuth verifier could not be decrypted") from error
        tokens, profile = provider.complete(
            code=code,
            redirect_uri=attempt.redirect_uri,
            code_verifier=code_verifier,
            nonce_hash=attempt.nonce_hash,
            callback_data=callback_data,
        )
        account = _resolve_account(provider, attempt, profile, tokens)
        login(request, account.user, backend="django.contrib.auth.backends.ModelBackend")
    except OAuthProviderError as error:
        if attempt is not None:
            attempt.error = str(error)[:255]
            attempt.save(update_fields=["error"])
        response: HttpResponse = JsonResponse({"detail": str(error)}, status=400)
    else:
        response = HttpResponseRedirect(attempt.next_url)
    _write_binding_cookie(response, request, provider, "", 0)
    return response
