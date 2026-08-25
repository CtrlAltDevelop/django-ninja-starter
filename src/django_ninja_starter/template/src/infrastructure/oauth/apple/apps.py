from django.apps import AppConfig

from infrastructure.common.appsettings import AppSettings, Requirement


class OAuthAppleConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "infrastructure.oauth.apple"
    label = "oauth_apple"
    verbose_name = "OAuth - Apple"

    settings_spec = AppSettings(
        title="Apple sign-in",
        summary="Authorization-code sign-in against Apple.",
        requirements=(
            Requirement(
                "OAUTH_PROVIDER_CONFIG.apple.client_id",
                env="APPLE_OAUTH_CLIENT_ID",
                purpose="the Services ID registered with Apple",
                required=True,
            ),
            Requirement(
                "OAUTH_PROVIDER_CONFIG.apple.team_id",
                env="APPLE_OAUTH_TEAM_ID",
                purpose="the Apple developer team that owns the key",
                required=True,
            ),
            Requirement(
                "OAUTH_PROVIDER_CONFIG.apple.key_id",
                env="APPLE_OAUTH_KEY_ID",
                purpose="identifies which private key signs the client secret",
                required=True,
            ),
            Requirement(
                "OAUTH_PROVIDER_CONFIG.apple.private_key",
                env="APPLE_OAUTH_PRIVATE_KEY",
                purpose="signs the short-lived client secret Apple requires",
                required=True,
            ),
            Requirement(
                "OAUTH_PROVIDER_CONFIG.apple.redirect_uri",
                env="APPLE_OAUTH_REDIRECT_URI",
                purpose="the exact callback URL registered with the provider",
                recommended=True,
                hint=(
                    "Left empty the callback is derived from the incoming request, which "
                    "a spoofed Host header can influence."
                ),
            ),
            Requirement(
                "OAUTH_PROVIDER_CONFIG.apple.scopes",
                env="APPLE_OAUTH_SCOPES",
                purpose="what this application asks the provider for",
                required=True,
            ),
        ),
    )
