from django.apps import AppConfig

from infrastructure.common.appsettings import AppSettings, Requirement


class OAuthMicrosoftConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "infrastructure.oauth.microsoft"
    label = "oauth_microsoft"
    verbose_name = "OAuth - Microsoft"

    settings_spec = AppSettings(
        title="Microsoft sign-in",
        summary="Authorization-code sign-in against Microsoft.",
        requirements=(
            Requirement(
                "OAUTH_PROVIDER_CONFIG.microsoft.client_id",
                env="MICROSOFT_OAUTH_CLIENT_ID",
                purpose="identifies this application to Microsoft",
                required=True,
            ),
            Requirement(
                "OAUTH_PROVIDER_CONFIG.microsoft.client_secret",
                env="MICROSOFT_OAUTH_CLIENT_SECRET",
                purpose="proves the token request came from this application",
                required=True,
            ),
            Requirement(
                "OAUTH_PROVIDER_CONFIG.microsoft.tenant",
                env="MICROSOFT_OAUTH_TENANT",
                purpose="which directory may sign in: common, organizations, consumers, or a GUID",
                required=True,
            ),
            Requirement(
                "OAUTH_PROVIDER_CONFIG.microsoft.redirect_uri",
                env="MICROSOFT_OAUTH_REDIRECT_URI",
                purpose="the exact callback URL registered with the provider",
                recommended=True,
                hint=(
                    "Left empty the callback is derived from the incoming request, which "
                    "a spoofed Host header can influence."
                ),
            ),
            Requirement(
                "OAUTH_PROVIDER_CONFIG.microsoft.scopes",
                env="MICROSOFT_OAUTH_SCOPES",
                purpose="what this application asks the provider for",
                required=True,
            ),
        ),
    )
