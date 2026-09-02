from django.apps import AppConfig


class CmsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.cms"
    verbose_name = "Content"

    # The settings this app reads, as plain rows rather than as a class imported
    # from the project: documenting them must not be the thing that stops the
    # directory being copied somewhere else.
    settings_docs = (
        (
            "DJANGO_CMS_ENABLED",
            "**Yes**",
            "Installs the app, its migrations, its routes and its admin. Unset, "
            "a project carries no CMS at all",
        ),
        (
            "DJANGO_CMS_LANGUAGES",
            "Optional",
            "The languages content may be written in, most preferred first. "
            "Defaults to `LANGUAGE_CODE`",
        ),
        (
            "DJANGO_CMS_PREVIEW_TTL_SECONDS",
            "Optional",
            "How long a preview link opens a draft for. Defaults to a day",
        ),
    )
