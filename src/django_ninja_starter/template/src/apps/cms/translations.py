"""Which languages the content is written in, and how one is chosen per request.

The site's languages are their own setting rather than Django's ``LANGUAGES``,
which defaults to every language Django ships a name for. Reusing it would mean
"required in every language" quietly meant a hundred of them, and that a typo in
an admin form was accepted as a real translation. ``CMS_LANGUAGES`` is the short
list an editor is actually expected to fill in.

Reading a translation never returns nothing when *something* was written. A
request for ``en-GB`` takes ``en-GB``, then any other ``en``, then the default
language, and only then gives up -- so a page that has been translated once is
readable by everybody, and a client asking for a language nobody has typed yet
gets the fallback instead of a hole in the layout.
"""

from collections.abc import Mapping

from django.conf import settings
from django.http import HttpRequest


def normalize_language(code: str) -> str:
    """Lower-case a language tag and settle on hyphens: ``en_US`` -> ``en-us``."""
    return code.strip().lower().replace("_", "-")


def base_language(code: str) -> str:
    """``en-gb`` -> ``en``. What two tags have in common when they are related."""
    return normalize_language(code).partition("-")[0]


def known_languages() -> list[str]:
    """Every language content may be written in, most preferred first.

    Defaulted rather than required, so this app works in a project that has
    never heard of it: one language, the project's own.
    """
    configured = [
        normalize_language(code)
        for code in getattr(settings, "CMS_LANGUAGES", None) or []
        if code.strip()
    ]
    return list(dict.fromkeys(configured)) or [normalize_language(settings.LANGUAGE_CODE)]


def default_language() -> str:
    """The language a reader gets when their own is not among the translations."""
    languages = known_languages()
    preferred = normalize_language(settings.LANGUAGE_CODE)
    return preferred if preferred in languages else languages[0]


def match_language(code: str | None) -> str | None:
    """Resolve one requested tag to a configured language, or ``None``."""
    if not code:
        return None
    wanted = normalize_language(code)
    languages = known_languages()
    if wanted in languages:
        return wanted
    base = base_language(wanted)
    return next((language for language in languages if base_language(language) == base), None)


def _accepted_languages(header: str) -> list[str]:
    """Parse ``Accept-Language`` into tags, most wanted first."""
    ranked: list[tuple[float, int, str]] = []
    for position, part in enumerate(header.split(",")):
        tag, _, parameters = part.strip().partition(";")
        if not tag or tag == "*":
            continue
        quality = 1.0
        for parameter in parameters.split(";"):
            key, _, value = parameter.partition("=")
            if key.strip() == "q":
                try:
                    quality = float(value)
                except ValueError:
                    quality = 0.0
        if quality > 0:
            # Position breaks ties, so equally weighted tags keep the client's order.
            ranked.append((-quality, position, tag.strip()))
    return [tag for _, _, tag in sorted(ranked)]


def resolve_language(request: HttpRequest, requested: str | None = None) -> str:
    """Pick the language for one request.

    An explicit ``?language=`` wins, because a client that asks for a specific
    translation means it -- a language switcher must not be overruled by the
    browser's preferences. ``Accept-Language`` decides for everybody else.
    """
    if (explicit := match_language(requested)) is not None:
        return explicit
    for tag in _accepted_languages(request.headers.get("Accept-Language", "")):
        if (matched := match_language(tag)) is not None:
            return matched
    return default_language()


def translation[T](values: Mapping[str, T], language: str) -> T | None:
    """Read one language out of a ``{language: value}`` mapping, with fallbacks."""
    if not values:
        return None
    wanted = normalize_language(language)
    if wanted in values:
        return values[wanted]
    base = base_language(wanted)
    for code, value in values.items():
        if base_language(code) == base:
            return value
    fallback = default_language()
    if fallback in values:
        return values[fallback]
    return None


def translated_languages(values: Mapping[str, object]) -> list[str]:
    """The configured languages this mapping actually has content for."""
    return [language for language in known_languages() if values.get(language) not in (None, "")]
