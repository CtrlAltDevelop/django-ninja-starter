"""Low-level helpers used by the independently installable OAuth providers."""

import secrets
from typing import Any
from urllib.parse import urlencode

import httpx
import jwt
from django.conf import settings

from infrastructure.oauth_core.social import OAuthProviderError, ProviderTokens
from infrastructure.oauth_core.tokens import hash_token


class AuthorizationCodeProvider:
    key = ""
    account_model = ""
    authorization_endpoint = ""
    token_endpoint = ""
    userinfo_endpoint = ""
    jwks_endpoint = ""
    issuer: str | list[str] = ""
    uses_nonce = True
    uses_pkce = True

    @property
    def config(self) -> dict[str, Any]:
        return settings.OAUTH_PROVIDER_CONFIG[self.key]

    def is_configured(self) -> bool:
        return bool(self.config.get("client_id") and self.client_credential())

    def client_credential(self) -> str:
        return str(self.config.get("client_secret", ""))

    def extra_authorization_params(self) -> dict[str, str]:
        return {}

    def authorization_url(
        self,
        *,
        state: str,
        nonce: str,
        code_challenge: str,
        redirect_uri: str,
    ) -> str:
        parameters = {
            "client_id": str(self.config["client_id"]),
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "scope": " ".join(self.config["scopes"]),
            "state": state,
            **self.extra_authorization_params(),
        }
        if nonce:
            parameters["nonce"] = nonce
        if code_challenge:
            parameters.update({"code_challenge": code_challenge, "code_challenge_method": "S256"})
        return f"{self.authorization_endpoint}?{urlencode(parameters)}"

    def exchange_code(
        self,
        *,
        code: str,
        redirect_uri: str,
        code_verifier: str,
    ) -> ProviderTokens:
        payload = {
            "client_id": str(self.config["client_id"]),
            "client_secret": self.client_credential(),
            "code": code,
            "grant_type": "authorization_code",
            "redirect_uri": redirect_uri,
        }
        if code_verifier:
            payload["code_verifier"] = code_verifier
        try:
            response = httpx.post(
                self.token_endpoint,
                data=payload,
                headers={"Accept": "application/json"},
                timeout=settings.OAUTH_HTTP_TIMEOUT_SECONDS,
            )
            response.raise_for_status()
            body = response.json()
        except (httpx.HTTPError, ValueError) as error:
            raise OAuthProviderError(f"{self.key} token exchange failed") from error
        if body.get("error") or not body.get("access_token"):
            raise OAuthProviderError(
                str(body.get("error_description") or body.get("error") or "Missing access token")
            )
        scope_value = body.get("scope", "")
        if isinstance(scope_value, str):
            scopes = scope_value.replace(",", " ").split()
        elif isinstance(scope_value, list):
            scopes = [str(scope) for scope in scope_value]
        else:
            scopes = []
        try:
            expires_in = int(body["expires_in"]) if body.get("expires_in") is not None else None
        except (TypeError, ValueError) as error:
            raise OAuthProviderError(f"{self.key} returned an invalid token lifetime") from error
        if expires_in is not None and expires_in <= 0:
            raise OAuthProviderError(f"{self.key} returned an invalid token lifetime")
        return ProviderTokens(
            access_token=str(body["access_token"]),
            refresh_token=str(body.get("refresh_token", "")),
            expires_in=expires_in,
            scopes=scopes or list(self.config["scopes"]),
            id_token=str(body.get("id_token", "")),
        )

    def fetch_json(self, url: str, access_token: str) -> dict[str, Any]:
        try:
            response = httpx.get(
                url,
                headers={
                    "Accept": "application/json",
                    "Authorization": f"Bearer {access_token}",
                    "User-Agent": "django-ninja-starter",
                },
                timeout=settings.OAUTH_HTTP_TIMEOUT_SECONDS,
            )
            response.raise_for_status()
            body = response.json()
        except (httpx.HTTPError, ValueError) as error:
            raise OAuthProviderError(f"{self.key} profile request failed") from error
        if not isinstance(body, dict):
            raise OAuthProviderError(f"{self.key} returned an invalid profile")
        return body

    def decode_id_token(
        self,
        id_token: str,
        nonce_hash: str,
        *,
        issuer: str | list[str] | None = None,
        jwks_endpoint: str | None = None,
        verify_issuer: bool = True,
    ) -> dict[str, Any]:
        if not id_token:
            raise OAuthProviderError(f"{self.key} did not return an ID token")
        try:
            signing_key = jwt.PyJWKClient(
                jwks_endpoint or self.jwks_endpoint,
                timeout=settings.OAUTH_HTTP_TIMEOUT_SECONDS,
            ).get_signing_key_from_jwt(id_token)
            claims = jwt.decode(
                id_token,
                signing_key.key,
                algorithms=["RS256", "ES256"],
                audience=str(self.config["client_id"]),
                issuer=issuer or self.issuer,
                leeway=settings.OAUTH_CLOCK_SKEW_SECONDS,
                options={
                    "verify_iss": verify_issuer,
                    "require": ["iss", "sub", "aud", "exp", "iat"],
                },
            )
        except jwt.PyJWTError as error:
            raise OAuthProviderError(f"Invalid {self.key} ID token") from error
        nonce = str(claims.get("nonce", ""))
        if nonce_hash and not secrets.compare_digest(hash_token(nonce), nonce_hash):
            raise OAuthProviderError(f"Invalid {self.key} nonce")
        authorized_party = str(claims.get("azp", ""))
        audience = claims.get("aud")
        requires_authorized_party = (isinstance(audience, list) and len(audience) > 1) or bool(
            authorized_party
        )
        if requires_authorized_party and not secrets.compare_digest(
            authorized_party, str(self.config["client_id"])
        ):
            raise OAuthProviderError(f"Invalid {self.key} authorized party")
        return dict(claims)
