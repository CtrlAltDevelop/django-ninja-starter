from typing import Any

import httpx
from django.conf import settings

from infrastructure.oauth.core.provider import AuthorizationCodeProvider
from infrastructure.oauth.core.social import OAuthProviderError, ProviderTokens, SocialProfile


class GitHubProvider(AuthorizationCodeProvider):
    key = "github"
    account_model = "oauth_github.GitHubAccount"
    authorization_endpoint = "https://github.com/login/oauth/authorize"
    token_endpoint = "https://github.com/login/oauth/access_token"
    userinfo_endpoint = "https://api.github.com/user"
    uses_nonce = False

    def _primary_email(self, access_token: str) -> tuple[str, bool]:
        try:
            response = httpx.get(
                "https://api.github.com/user/emails",
                headers={
                    "Accept": "application/vnd.github+json",
                    "Authorization": f"Bearer {access_token}",
                    "X-GitHub-Api-Version": "2022-11-28",
                    "User-Agent": "django-ninja-starter",
                },
                timeout=settings.OAUTH_HTTP_TIMEOUT_SECONDS,
            )
            response.raise_for_status()
            emails = response.json()
        except (httpx.HTTPError, ValueError) as error:
            raise OAuthProviderError("GitHub email request failed") from error
        for item in emails if isinstance(emails, list) else []:
            if item.get("primary"):
                return str(item.get("email", "")), bool(item.get("verified", False))
        return "", False

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
        claims: dict[str, Any] = self.fetch_json(self.userinfo_endpoint, tokens.access_token)
        if not claims.get("id"):
            raise OAuthProviderError("GitHub profile is missing its stable account ID")
        email = str(claims.get("email") or "")
        verified = False
        try:
            primary_email, verified = self._primary_email(tokens.access_token)
            if primary_email:
                email = primary_email
        except OAuthProviderError:
            if not email:
                raise
        return tokens, SocialProfile(
            subject=str(claims["id"]),
            email=email,
            email_verified=verified,
            display_name=str(claims.get("name") or claims.get("login") or ""),
            avatar_url=str(claims.get("avatar_url", "")),
            claims=claims,
        )


provider = GitHubProvider()
