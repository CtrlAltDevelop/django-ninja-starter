import re
from typing import Any

import jwt

from infrastructure.oauth.core.provider import AuthorizationCodeProvider
from infrastructure.oauth.core.social import OAuthProviderError, ProviderTokens, SocialProfile

TENANT_ID_PATTERN = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
    r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)
CONSUMER_TENANT_ID = "9188040d-6c67-4c5b-b112-36a304b66dad"


class MicrosoftProvider(AuthorizationCodeProvider):
    key = "microsoft"
    account_model = "oauth_microsoft.MicrosoftAccount"
    jwks_endpoint = "https://login.microsoftonline.com/common/discovery/v2.0/keys"

    @property
    def tenant(self) -> str:
        return str(self.config.get("tenant", "common"))

    # Read-only overrides: the endpoints follow whatever tenant is configured now,
    # not the one that happened to be loaded when this module was first imported.
    @property
    def authorization_endpoint(self) -> str:  # type: ignore[override]
        return f"https://login.microsoftonline.com/{self.tenant}/oauth2/v2.0/authorize"

    @property
    def token_endpoint(self) -> str:  # type: ignore[override]
        return f"https://login.microsoftonline.com/{self.tenant}/oauth2/v2.0/token"

    def complete(
        self,
        *,
        code: str,
        redirect_uri: str,
        code_verifier: str,
        nonce_hash: str,
        callback_data: dict[str, str],
    ) -> tuple[ProviderTokens, SocialProfile]:
        tokens = self.exchange_code(
            code=code, redirect_uri=redirect_uri, code_verifier=code_verifier
        )
        try:
            unverified: dict[str, Any] = jwt.decode(
                tokens.id_token,
                options={
                    "verify_signature": False,
                    "verify_aud": False,
                    "verify_iss": False,
                },
            )
        except jwt.PyJWTError as error:
            raise OAuthProviderError("Invalid Microsoft ID token") from error
        tenant_id = str(unverified.get("tid", ""))
        if not TENANT_ID_PATTERN.fullmatch(tenant_id):
            raise OAuthProviderError("Invalid Microsoft tenant claim")
        expected_issuer = f"https://login.microsoftonline.com/{tenant_id}/v2.0"
        configured_tenant = self.tenant.casefold()
        if configured_tenant not in {"common", "organizations", "consumers"} and not (
            TENANT_ID_PATTERN.fullmatch(configured_tenant)
        ):
            raise OAuthProviderError("Microsoft tenant must be a tenant ID or supported alias")
        if TENANT_ID_PATTERN.fullmatch(configured_tenant) and (
            tenant_id.casefold() != configured_tenant
        ):
            raise OAuthProviderError("Microsoft token came from an unexpected tenant")
        if configured_tenant == "organizations" and tenant_id.casefold() == CONSUMER_TENANT_ID:
            raise OAuthProviderError("Microsoft consumer accounts are not allowed")
        if configured_tenant == "consumers" and tenant_id.casefold() != CONSUMER_TENANT_ID:
            raise OAuthProviderError("Microsoft organizational accounts are not allowed")
        claims: dict[str, Any] = self.decode_id_token(
            tokens.id_token,
            nonce_hash,
            issuer=expected_issuer,
            jwks_endpoint=(f"https://login.microsoftonline.com/{tenant_id}/discovery/v2.0/keys"),
        )
        return tokens, SocialProfile(
            subject=str(claims["sub"]),
            email=str(claims.get("email") or claims.get("preferred_username") or ""),
            email_verified=False,
            display_name=str(claims.get("name", "")),
            claims=claims,
        )


provider = MicrosoftProvider()
