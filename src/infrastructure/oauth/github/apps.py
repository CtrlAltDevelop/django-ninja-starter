from django.apps import AppConfig

from infrastructure.common.appsettings import AppSettings, Requirement


class OAuthGitHubConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "infrastructure.oauth.github"
    label = "oauth_github"
    verbose_name = "OAuth - GitHub"

    settings_spec = AppSettings(
        title="GitHub sign-in",
        summary="Authorization-code sign-in against GitHub.",
        requirements=(
            Requirement(
                "OAUTH_PROVIDER_CONFIG.github.client_id",
                env="GITHUB_OAUTH_CLIENT_ID",
                purpose="identifies this application to GitHub",
                required=True,
            ),
            Requirement(
                "OAUTH_PROVIDER_CONFIG.github.client_secret",
                env="GITHUB_OAUTH_CLIENT_SECRET",
                purpose="proves the token request came from this application",
                required=True,
            ),
            Requirement(
                "OAUTH_PROVIDER_CONFIG.github.redirect_uri",
                env="GITHUB_OAUTH_REDIRECT_URI",
                purpose="the exact callback URL registered with the provider",
                recommended=True,
                hint=(
                    "Left empty the callback is derived from the incoming request, which "
                    "a spoofed Host header can influence."
                ),
            ),
            Requirement(
                "OAUTH_PROVIDER_CONFIG.github.scopes",
                env="GITHUB_OAUTH_SCOPES",
                purpose="what this application asks the provider for",
                required=True,
            ),
        ),
    )
