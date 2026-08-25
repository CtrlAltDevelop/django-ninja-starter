"""Let an app declare the settings it cannot work without.

Every optional app here ships defaults that make it start, because a starter that
will not boot is useless. But a default that is *safe* in development is often
wrong in production -- codes written to the log instead of texted, a challenge
store that forgets everything on restart, a signing key derived from the secret
key. And some settings have no sensible default at all: nobody can guess an OAuth
client secret.

So each app states its own requirements on its ``AppConfig``, and one system
check reads them. The value of putting it there rather than in a central list is
that enabling an app is what activates its requirements: a deployment is told
exactly what the apps *it turned on* still need, and never nagged about the rest.

    class AuthMagicLinkConfig(AppConfig):
        ...
        settings_spec = AppSettings(
            title="Magic-link login",
            requirements=(
                Requirement(
                    "AUTH_MAGIC_LINK_BASE_URL",
                    env="DJANGO_AUTH_MAGIC_LINK_BASE_URL",
                    purpose="the page that reads the token out of the URL",
                    required=True,
                ),
            ),
        )
"""

from dataclasses import dataclass, field
from typing import Any

from django.conf import settings

MISSING = object()


@dataclass(frozen=True)
class Requirement:
    """One setting an app needs, and what counts as an acceptable value."""

    setting: str
    """Dotted path. The first segment is the Django setting; later segments index
    into it, so a provider's own key is reachable as
    ``OAUTH_PROVIDER_CONFIG.google.client_secret``."""

    purpose: str
    """What the value is for, said in a way that helps somebody fill it in."""

    env: str = ""
    """The environment variable that supplies it, named in the hint."""

    required: bool = False
    """Empty is an error: the app cannot do its job without this."""

    recommended: bool = False
    """Empty is a warning: the app works, but not the way it should in production."""

    minimum: float | None = None
    maximum: float | None = None
    choices: tuple[str, ...] = ()

    unsafe_defaults: tuple[str, ...] = ()
    """Values that are fine locally and wrong once real users arrive."""

    hint: str = ""

    @property
    def check_id(self) -> str:
        """A stable id, so a deployment can silence one message and not a category."""
        return self.setting

    def resolve(self) -> Any:
        """Return the configured value, or :data:`MISSING` if the path does not exist."""
        head, *rest = self.setting.split(".")
        value: Any = getattr(settings, head, MISSING)
        for key in rest:
            if not isinstance(value, dict) or key not in value:
                return MISSING
            value = value[key]
        return value


@dataclass(frozen=True)
class AppSettings:
    """The settings contract for one app."""

    title: str
    summary: str = ""
    requirements: tuple[Requirement, ...] = field(default_factory=tuple)
