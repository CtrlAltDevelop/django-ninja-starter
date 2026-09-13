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

from collections.abc import Callable
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

    pattern: str = ""
    """A regular expression the value has to match whole.

    For the settings whose acceptable values are a shape rather than a list: a
    currency is any three letters, a mount path is anything starting with a
    slash. ``choices`` would have to enumerate the world to say the same thing.
    """

    pattern_description: str = ""
    """The pattern said in words, because a regex in an error message helps nobody."""

    unsafe_defaults: tuple[str, ...] = ()
    """Values that are fine locally and wrong once real users arrive."""

    hint: str = ""

    applies_when: Callable[[], bool] | None = None
    """Whether this requirement is live at all, given the rest of the settings.

    Some settings only matter once another one has a particular value: a Redis
    URL is nothing to nag about until the broker is the Redis one, and a
    deployment told to fill in a setting its own configuration has made
    irrelevant learns to ignore the checker. Called with no arguments, at check
    time, so it reads the settings as they finally are.
    """

    def applies(self) -> bool:
        """Whether this requirement should be validated in this configuration."""
        return self.applies_when is None or self.applies_when()

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
class Rule:
    """A condition across several of an app's settings at once.

    A requirement can only judge one value on its own, and some misconfigurations
    are relationships: a default page size above the ceiling that clamps it is
    two individually reasonable numbers in the wrong order. ``holds`` is called
    with no arguments at check time and returns whether the configuration is
    sound.
    """

    holds: Callable[[], bool]
    message: str
    """What is wrong, in the deployment's terms rather than the code's."""

    settings: tuple[str, ...] = ()
    """The settings involved, first one naming the check so it can be silenced."""

    hint: str = ""

    @property
    def check_id(self) -> str:
        return self.settings[0] if self.settings else "settings"


@dataclass(frozen=True)
class AppSettings:
    """The settings contract for one app."""

    title: str
    summary: str = ""
    requirements: tuple[Requirement, ...] = field(default_factory=tuple)
    rules: tuple[Rule, ...] = field(default_factory=tuple)
    """Conditions no single requirement can express, checked once all are filled."""
