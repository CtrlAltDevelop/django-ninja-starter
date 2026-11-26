from django.apps import AppConfig

from infrastructure.common.appsettings import AppSettings, Requirement


class FilesConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.files"
    label = "files"
    verbose_name = "Files"

    settings_spec = AppSettings(
        title="Files",
        summary=(
            "Owned file uploads over REST, GraphQL and gRPC, stored through Django's "
            "storage API: local disk by default, S3 when configured."
        ),
        requirements=(
            Requirement(
                "FILES_ENABLED",
                env="DJANGO_FILES_ENABLED",
                purpose="whether this deployment carries file uploads at all",
                required=True,
                hint=(
                    "The app is installed, so something put it in INSTALLED_APPS while "
                    "DJANGO_FILES_ENABLED was off. Set it to true, or drop the app."
                ),
            ),
            Requirement(
                "FILES_UPLOAD_PATH",
                env="DJANGO_FILES_UPLOAD_PATH",
                purpose="the folder or key prefix inside the configured storage files go under",
                required=True,
            ),
            Requirement(
                "FILES_MAX_UPLOAD_MB",
                env="DJANGO_FILES_MAX_UPLOAD_MB",
                purpose="the largest file anybody may upload, in megabytes; zero means no limit",
                minimum=0,
            ),
        ),
    )
