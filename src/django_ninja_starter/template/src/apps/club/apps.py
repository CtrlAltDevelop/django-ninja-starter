"""The app, its settings contract, and where the event registry gets filled.

`ready` is the one place bridges are loaded, and each built-in one is guarded by
`apps.is_installed`. That guard is what keeps this app isolated: the club runs
perfectly well with no other optional app installed, registering only the events
every deployment has, and a mission written against an event whose app is not
here is refused rather than quietly dead.
"""

from django.apps import AppConfig, apps
from django.conf import settings

from infrastructure.common.appsettings import AppSettings, Requirement, Rule


def _sources() -> list[str]:
    return [path for path in getattr(settings, "CLUB_EVENT_SOURCES", ()) if path.strip()]


def _sources_import() -> bool:
    """Whether every module named in the setting can actually be imported."""
    from importlib import import_module

    for path in _sources():
        try:
            import_module(path)
        except Exception:
            return False
    return True


class ClubConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.club"
    label = "club"
    verbose_name = "Club"

    settings_spec = AppSettings(
        title="Club",
        summary=(
            "Clubs with a levelled ladder, and missions that complete themselves from "
            "events other apps emit."
        ),
        requirements=(
            Requirement(
                "CLUB_ENABLED",
                env="DJANGO_CLUB_ENABLED",
                purpose=(
                    "whether this deployment carries clubs at all -- their tables, "
                    "their routes and their admin"
                ),
                required=True,
                hint=(
                    "The app is installed, so something put it in INSTALLED_APPS while "
                    "DJANGO_CLUB_ENABLED was off. Set it to true, or drop the app: half "
                    "enabled, it migrates its tables and publishes none of its routes."
                ),
            ),
            Requirement(
                "CLUB_EVENT_SOURCES",
                env="DJANGO_CLUB_EVENT_SOURCES",
                purpose=(
                    "dotted module paths that register your own app's events, so "
                    "missions can be built out of what your code does"
                ),
                hint=(
                    "Comma-separated, for example myapp.club_events. Each is imported "
                    "once at startup; importing it is what registers its events."
                ),
            ),
            Requirement(
                "CLUB_JOIN_ON_SIGNUP",
                env="DJANGO_CLUB_JOIN_ON_SIGNUP",
                purpose=(
                    "the slug of a club every new account is put in as it is created, "
                    "so a welcome can be earned by signing up"
                ),
                hint=(
                    "Empty puts nobody anywhere. A slug that names no club, or an "
                    "archived one, is logged and the account is left in none."
                ),
            ),
            Requirement(
                "CLUB_PAGE_SIZE",
                env="DJANGO_CLUB_PAGE_SIZE",
                purpose="how many rows a listing returns when the caller does not say",
                minimum=1,
                maximum=200,
            ),
            Requirement(
                "CLUB_MAX_PAGE_SIZE",
                env="DJANGO_CLUB_MAX_PAGE_SIZE",
                purpose="the ceiling on `limit`, so one request cannot ask for every member",
                minimum=1,
                maximum=1_000,
            ),
        ),
        rules=(
            Rule(
                holds=lambda: settings.CLUB_MAX_PAGE_SIZE >= settings.CLUB_PAGE_SIZE,
                message=(
                    "CLUB_MAX_PAGE_SIZE is below CLUB_PAGE_SIZE, so the ceiling is under "
                    "the default and every listing is trimmed to it"
                ),
                settings=("CLUB_MAX_PAGE_SIZE", "CLUB_PAGE_SIZE"),
                hint="Raise DJANGO_CLUB_MAX_PAGE_SIZE, or lower DJANGO_CLUB_PAGE_SIZE.",
            ),
            Rule(
                holds=_sources_import,
                message=(
                    "CLUB_EVENT_SOURCES names a module that cannot be imported, so the "
                    "events it was meant to register are missing and any mission built "
                    "on one of them can never fire"
                ),
                settings=("CLUB_EVENT_SOURCES",),
                hint=(
                    "Check the dotted path in DJANGO_CLUB_EVENT_SOURCES. It is imported "
                    "at startup, and importing it is what registers its events."
                ),
            ),
        ),
    )

    def ready(self) -> None:
        """Fill the event registry: the floor, then each installed app, then yours."""
        from apps.club.bridges import accounts

        accounts.connect()
        if apps.is_installed("apps.shop"):
            from apps.club.bridges import shop

            shop.connect()
        if apps.is_installed("apps.wallet"):
            from apps.club.bridges import wallet

            wallet.connect()
        from apps.club.events import load

        load(_sources())
