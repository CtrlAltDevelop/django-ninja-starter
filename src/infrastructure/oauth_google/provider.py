from typing import Any

from infrastructure.oauth_core.provider import AuthorizationCodeProvider
from infrastructure.oauth_core.social import ProviderTokens, SocialProfile


class GoogleProvider(AuthorizationCodeProvider):
    key = "google"
    account_model = "oauth_google.GoogleAccount"
    authorization_endpoint = "https://accounts.google.com/o/oauth2/v2/auth"
    token_endpoint = "https://oauth2.googleapis.com/token"
    jwks_endpoint = "https://www.googleapis.com/oauth2/v3/certs"
    issuer = ["https://accounts.google.com", "accounts.google.com"]

    def extra_authorization_params(self) -> dict[str, str]:
        return {"access_type": "offline", "include_granted_scopes": "true"}

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
        claims: dict[str, Any] = self.decode_id_token(tokens.id_token, nonce_hash)
        return tokens, SocialProfile(
            subject=str(claims["sub"]),
            email=str(claims.get("email", "")),
            email_verified=str(claims.get("email_verified", "false")).lower() == "true",
            display_name=str(claims.get("name", "")),
            avatar_url=str(claims.get("picture", "")),
            claims=claims,
        )


provider = GoogleProvider()
