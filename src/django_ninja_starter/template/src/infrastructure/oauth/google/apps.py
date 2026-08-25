from django.apps import AppConfig

from infrastructure.common.appsettings import AppSettings, Requirement


class OAuthGoogleConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "infrastructure.oauth.google"
    label = "oauth_google"
    verbose_name = "OAuth - Google"

    settings_spec = AppSettings(
        title="Google sign-in",
        summary="Authorization-code sign-in against Google.",
        requirements=(
            Requirement(
                "OAUTH_PROVIDER_CONFIG.google.client_id",
                env="GOOGLE_OAUTH_CLIENT_ID",
                purpose="identifies this application to Google",
                required=True,
            ),
            Requirement(
                "OAUTH_PROVIDER_CONFIG.google.client_secret",
                env="GOOGLE_OAUTH_CLIENT_SECRET",
                purpose="proves the token request came from this application",
                required=True,
            ),
            Requirement(
                "OAUTH_PROVIDER_CONFIG.google.redirect_uri",
                env="GOOGLE_OAUTH_REDIRECT_URI",
                purpose="the exact callback URL registered with the provider",
                recommended=True,
                hint=(
                    "Left empty the callback is derived from the incoming request, which "
                    "a spoofed Host header can influence."
                ),
            ),
            Requirement(
                "OAUTH_PROVIDER_CONFIG.google.scopes",
                env="GOOGLE_OAUTH_SCOPES",
                purpose="what this application asks the provider for",
                required=True,
            ),
        ),
    )
