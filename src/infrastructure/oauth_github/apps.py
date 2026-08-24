from django.apps import AppConfig


class OAuthGitHubConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "infrastructure.oauth_github"
    verbose_name = "OAuth - GitHub"
