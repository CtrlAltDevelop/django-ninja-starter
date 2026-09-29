from django.apps import AppConfig

from infrastructure.common.appsettings import AppSettings, Requirement


class CmsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.cms"
    label = "cms"
    verbose_name = "Content"

    settings_spec = AppSettings(
        title="Content",
        summary="Pages an editor builds from typed sections, menus, and site metadata.",
        requirements=(
            Requirement(
                "CMS_ENABLED",
                env="DJANGO_CMS_ENABLED",
                purpose=(
                    "whether this deployment carries the CMS at all -- its tables, its "
                    "routes and its admin"
                ),
                required=True,
                hint=(
                    "The app is installed, so something put it in INSTALLED_APPS while "
                    "DJANGO_CMS_ENABLED was off. Set it to true, or drop the app: a half "
                    "enabled CMS migrates its tables and publishes none of its routes."
                ),
            ),
            Requirement(
                "CMS_LANGUAGES",
                env="DJANGO_CMS_LANGUAGES",
                purpose="the languages content may be written in, most preferred first",
                hint=(
                    "Left empty the app writes everything in LANGUAGE_CODE, which is "
                    "right for a single-language site and silently loses the point of "
                    "the per-field fallback for any other."
                ),
            ),
            Requirement(
                "CMS_PREVIEW_TTL_SECONDS",
                env="DJANGO_CMS_PREVIEW_TTL_SECONDS",
                purpose="how long a preview link opens a draft for",
                minimum=1,
                maximum=60 * 60 * 24 * 30,
                hint=(
                    "A preview link is an unauthenticated view of unpublished content, so "
                    "it is a window, not a door: keep it short enough that a forwarded "
                    "link stops working."
                ),
            ),
            Requirement(
                "CMS_UPLOAD_PATH",
                env="DJANGO_CMS_UPLOAD_PATH",
                purpose=(
                    "where a file uploaded on the content screen is written inside "
                    'STORAGES["default"]'
                ),
                required=True,
                hint=(
                    "A prefix, not a filesystem path -- a folder under MEDIA_ROOT locally "
                    "and a key prefix in a bucket in production. Empty would write "
                    "uploads to the root of the store, beside everything else that lives "
                    "there."
                ),
            ),
            Requirement(
                "CMS_MAX_UPLOAD_MB",
                env="DJANGO_CMS_MAX_UPLOAD_MB",
                purpose="the largest file the content screen accepts, in megabytes",
                minimum=0,
                maximum=1024,
                hint=(
                    "Zero means no limit, for a deployment whose proxy or storage imposes "
                    "one already and would rather have a single answer to 'how big may "
                    "this be' than two."
                ),
            ),
        ),
    )
