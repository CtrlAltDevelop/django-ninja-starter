import json
from datetime import UTC, datetime, timedelta
from typing import Any

import jwt

from infrastructure.oauth.core.provider import AuthorizationCodeProvider
from infrastructure.oauth.core.social import OAuthProviderError, ProviderTokens, SocialProfile


class AppleProvider(AuthorizationCodeProvider):
    key = "apple"
    account_model = "oauth_apple.AppleAccount"
    authorization_endpoint = "https://appleid.apple.com/auth/authorize"
    token_endpoint = "https://appleid.apple.com/auth/token"
    jwks_endpoint = "https://appleid.apple.com/auth/keys"
    issuer = "https://appleid.apple.com"
    uses_pkce = False
    uses_form_post = True

    def is_configured(self) -> bool:
        required = ("client_id", "team_id", "key_id", "private_key")
        return all(self.config.get(key) for key in required)

    def client_credential(self) -> str:
        now = datetime.now(UTC)
        private_key = str(self.config["private_key"]).replace("\\n", "\n")
        try:
            return jwt.encode(
                {
                    "iss": str(self.config["team_id"]),
                    "iat": now,
                    "exp": now + timedelta(days=30),
                    "aud": self.issuer,
                    "sub": str(self.config["client_id"]),
                },
                private_key,
                algorithm="ES256",
                headers={"kid": str(self.config["key_id"])},
            )
        except (TypeError, ValueError, jwt.PyJWTError) as error:
            raise OAuthProviderError("Apple OAuth private key is invalid") from error

    def extra_authorization_params(self) -> dict[str, str]:
        return {"response_mode": "form_post"}

    def complete(
        self,
        *,
        code: str,
        redirect_uri: str,
        code_verifier: str,
        nonce_hash: str,
        callback_data: dict[str, str],
    ) -> tuple[ProviderTokens, SocialProfile]:
        tokens = self.exchange_code(code=code, redirect_uri=redirect_uri, code_verifier="")
        claims: dict[str, Any] = self.decode_id_token(tokens.id_token, nonce_hash)
        apple_user: dict[str, Any] = {}
        try:
            parsed_user = json.loads(callback_data.get("user", "{}"))
            if isinstance(parsed_user, dict):
                apple_user = parsed_user
        except (TypeError, ValueError):
            pass
        name = apple_user.get("name", {})
        if not isinstance(name, dict):
            name = {}
        display_name = " ".join(
            part for part in (str(name.get("firstName", "")), str(name.get("lastName", ""))) if part
        )[:255]
        return tokens, SocialProfile(
            subject=str(claims["sub"]),
            email=str(claims.get("email", "")),
            email_verified=str(claims.get("email_verified", "false")).lower() == "true",
            display_name=display_name,
            claims={**claims, "apple_user": apple_user},
        )


provider = AppleProvider()
