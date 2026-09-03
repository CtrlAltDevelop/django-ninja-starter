"""The field types content is made of, and the one shape each of them stores.

Three ideas do most of the work here.

**A type describes one item; ``multiple`` says how many.** The obvious design is
a type per cardinality -- ``IMAGE`` and ``IMAGE_LIST``, ``FILE`` and
``FILE_LIST`` -- and it doubles the table while duplicating every validator.
Splitting cardinality out means a gallery is an image field that happens to hold
several, the validation is written once, and turning one picture into three is a
checkbox rather than a new type and a data migration.

**What is stored is canonical, not what was typed.** An editor may write a bare
URL where a link belongs; the normaliser here turns it into ``{"url": ...,
"title": null, "meta": {}}`` before it reaches the database. So a client can rely
on the shape of every value of a given type, and never has to handle "string or
object" for the same field on two different pages. Anything a normaliser cannot
make sense of raises, rather than being stored in a shape nobody will expect.

**A type is only as useful as the widget it earns.** Every member below is here
because it makes the editing screen more specific: ``TEXTAREA`` is a text box
with rows, ``COLOR`` is a colour picker, ``SELECT`` is a dropdown over the
field's own ``options``, ``IMAGE`` is an upload button beside a URL box. A type
that would render exactly like ``TEXT`` and validate exactly like ``TEXT`` is
not added, because then it is ``TEXT`` with a different label.

Every value keeps a ``meta`` object for the things this app has no opinion about
-- an image's dimensions, a link's ``rel``, a tracking id. That is the escape
hatch, so the named keys can stay small and typed.
"""

import re
from collections.abc import Callable, Mapping, Sequence
from datetime import date, datetime
from typing import Any

from django.core.exceptions import ValidationError
from django.core.validators import EmailValidator, URLValidator
from django.db import models
from django.utils.dateparse import parse_date, parse_datetime

LINK_KEYS = frozenset({"url", "title", "meta"})
MEDIA_KEYS = frozenset({"url", "title", "alt", "meta"})

#: ``#rgb`` or ``#rrggbb``. Kept to hex rather than accepting every CSS colour
#: notation, because the admin's colour input emits hex and a client that gets
#: one shape never has to parse ``rgb()`` as well.
COLOR = re.compile(r"^#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6})$")
#: Digits, and the punctuation people write between them -- including the
#: leading bracket of "(020) 7946 0958". Deliberately not E.164-only: a CMS
#: phone field is copy, and refusing a number written the way the country writes
#: it would mean the editor puts it in a text field instead and the type says
#: nothing at all. What is enforced is that it is mostly digits, which is
#: :data:`PHONE_DIGITS`.
PHONE = re.compile(r"^\+?[0-9(][0-9 ()./-]{2,30}$")
#: How many digits a string has to have before it is a phone number rather than
#: punctuation. Four is the shortest real number anybody publishes -- an
#: emergency line, an internal extension.
PHONE_DIGITS = 4
#: A page's slug, the same shape Django's ``SlugField`` accepts. Checked by
#: pattern rather than by lookup: a normaliser runs inside ``full_clean`` on
#: every write, and a query per value would make saving a page of references a
#: page of queries. A reference to a page that does not exist yet is also a
#: legitimate half-finished state -- the read side simply omits it.
SLUG = re.compile(r"^[-a-zA-Z0-9_]+$")
#: A root-relative address: ``/media/cms/uploads/image/hero-1a2b3c.png``, or
#: ``/about-us``. Accepted anywhere a URL is, because the commonest media value
#: in a project that stores its uploads locally is exactly this -- and a CMS that
#: refused what its own upload button produces would be absurd. ``//`` is
#: excluded on purpose: that is a protocol-relative *absolute* URL wearing a
#: path's clothes, and it is how a value that looks internal ends up pointing at
#: somebody else's host.
RELATIVE = re.compile(r"^/(?!/)[^\s]*$")


class FieldType(models.TextChoices):
    """What one item of a field's value is.

    The stored value is the member's string, not its position, so the wire
    format survives reordering this class -- and a client reading ``"image"``
    needs no lookup table to know what it has.
    """

    TEXT = "text", "Text"
    TEXTAREA = "textarea", "Text area"
    HTML = "html", "HTML"
    MARKDOWN = "markdown", "Markdown"
    SELECT = "select", "Choice"
    NUMBER = "number", "Number"
    BOOLEAN = "boolean", "Boolean"
    DATE = "date", "Date"
    DATETIME = "datetime", "Date and time"
    EMAIL = "email", "Email"
    PHONE = "phone", "Phone number"
    URL = "url", "URL"
    COLOR = "color", "Colour"
    LINK = "link", "Link"
    IMAGE = "image", "Image"
    VIDEO = "video", "Video"
    AUDIO = "audio", "Audio"
    FILE = "file", "File"
    PAGE = "page", "Page reference"
    CONTACT = "contact", "Contact details"
    JSON = "json", "Raw JSON"


#: The types whose value is one of ``{"url", "title", "alt", "meta"}`` -- an
#: uploaded or linked resource. Named here because three other modules ask the
#: question: the form renders an upload button for them, the admin groups them,
#: and the export command knows their value carries a URL worth rewriting.
MEDIA_TYPES = frozenset({FieldType.IMAGE, FieldType.VIDEO, FieldType.AUDIO, FieldType.FILE})
#: Media, plus the link that is a URL with a label rather than a resource.
ADDRESSABLE_TYPES = MEDIA_TYPES | {FieldType.LINK}
#: The types whose value is free-form and typed into a JSON box by hand.
OPEN_TYPES = frozenset({FieldType.CONTACT, FieldType.JSON})
#: The types that mean nothing without ``Field.options`` filled in.
CHOICE_TYPES = frozenset({FieldType.SELECT})
#: The types whose editing widget is a many-lined box rather than one line.
LONG_TYPES = frozenset({FieldType.TEXTAREA, FieldType.HTML, FieldType.MARKDOWN})
#: What an image, video, audio or file field will accept an upload of. Extensions
#: rather than MIME types, because a browser's reported type is the client's
#: claim and an extension is at least the name the file will be served under.
UPLOAD_EXTENSIONS: dict[Any, tuple[str, ...]] = {
    FieldType.IMAGE: (".avif", ".gif", ".jpeg", ".jpg", ".png", ".svg", ".webp"),
    FieldType.VIDEO: (".mov", ".mp4", ".ogv", ".webm"),
    FieldType.AUDIO: (".aac", ".flac", ".m4a", ".mp3", ".ogg", ".opus", ".wav"),
    FieldType.FILE: (),
}


def _text(value: Any) -> str:
    if not isinstance(value, str):
        raise ValidationError("Expected text.")
    return value


def _number(value: Any) -> int | float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ValidationError("Expected a number.")
    return value


def _boolean(value: Any) -> bool:
    if not isinstance(value, bool):
        raise ValidationError("Expected true or false.")
    return value


def _date(value: Any) -> str:
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if not isinstance(value, str) or parse_date(value) is None:
        raise ValidationError("Expected a date as YYYY-MM-DD.")
    return value


def _datetime(value: Any) -> str:
    if isinstance(value, datetime):
        return value.isoformat()
    if not isinstance(value, str) or parse_datetime(value) is None:
        raise ValidationError("Expected a date and time, for example 2026-09-01T10:30:00Z.")
    return value


def _email(value: Any) -> str:
    address = _text(value).strip()
    try:
        EmailValidator()(address)
    except ValidationError as error:
        raise ValidationError("Expected an email address.") from error
    return address


def _url(value: Any) -> str:
    """An absolute URL, or an address on this site.

    Both, because the two are the same answer to "where is this?" arrived at
    differently: a picture on a CDN and a picture this admin uploaded an hour
    ago are equally the hero image, and only one of them has a hostname.
    """
    url = _text(value).strip()
    if RELATIVE.match(url):
        return url
    try:
        URLValidator()(url)
    except ValidationError as error:
        raise ValidationError("Expected a URL, or an address beginning with /.") from error
    return url


def _phone(value: Any) -> str:
    """A phone number as somebody would write it, with the spacing left alone.

    Normalising to digits would be the tidier stored value and the wrong one: a
    CMS phone field is copy, and "+44 20 7946 0958" is how it is meant to be
    read out on the page.
    """
    number = _text(value).strip()
    digits = sum(character.isdigit() for character in number)
    if not PHONE.match(number) or digits < PHONE_DIGITS:
        raise ValidationError("Expected a phone number, for example +44 20 7946 0958.")
    return number


def _color(value: Any) -> str:
    """Hex, lower-cased, so two editors typing the same colour store the same string."""
    color = _text(value).strip()
    if not COLOR.match(color):
        raise ValidationError("Expected a colour as #rrggbb.")
    return color.lower()


def _page(value: Any) -> str:
    """Another page, named by its slug rather than addressed.

    The same choice the menu makes: a client routes its own pages, so a
    reference travels as the name the page API is asked for and renaming a
    slug is the one thing that breaks it -- which is visible, unlike a URL
    that quietly 404s.
    """
    slug = _text(value).strip()
    if not SLUG.match(slug):
        raise ValidationError("Expected a page slug, for example about-us.")
    return slug


def _json(value: Any) -> Any:
    """Anything JSON can hold, which is the point of the type.

    ``None`` is the one refusal: it is indistinguishable from "nothing written
    here", and :func:`is_empty` has already decided what that means.
    """
    if value is None:
        raise ValidationError("Expected a JSON value.")
    return value


def _meta(value: Any) -> dict[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise ValidationError("meta must be an object.")
    return dict(value)


def _optional_text(value: Any, key: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValidationError(f"{key} must be text.")
    return value


def _addressable(value: Any, allowed: frozenset[str]) -> dict[str, Any]:
    """Normalise a bare URL or an object into the full canonical shape."""
    if isinstance(value, str):
        value = {"url": value}
    if not isinstance(value, Mapping):
        raise ValidationError("Expected a URL or an object with a url.")
    unknown = set(value) - allowed
    if unknown:
        raise ValidationError(
            f"Unknown keys: {', '.join(sorted(unknown))}. "
            f"Allowed: {', '.join(sorted(allowed))}. Put anything else under meta."
        )
    normalised: dict[str, Any] = {
        "url": _url(value.get("url")),
        "title": _optional_text(value.get("title"), "title"),
        "meta": _meta(value.get("meta")),
    }
    if "alt" in allowed:
        normalised["alt"] = _optional_text(value.get("alt"), "alt")
    return normalised


def _link(value: Any) -> dict[str, Any]:
    return _addressable(value, LINK_KEYS)


def _media(value: Any) -> dict[str, Any]:
    return _addressable(value, MEDIA_KEYS)


def _contact(value: Any) -> dict[str, str]:
    if not isinstance(value, Mapping):
        raise ValidationError("Expected an object of contact details.")
    for key, item in value.items():
        if not isinstance(item, str):
            raise ValidationError(f"{key} must be text.")
    return dict(value)


# Keyed and looked up by the choice member, which is a string at runtime. The
# annotation says ``Any`` because Django is untyped here, so a checker sees the
# member as the ``(value, label)`` tuple it was written as.
NORMALISERS: dict[Any, Callable[[Any], Any]] = {
    FieldType.TEXT: _text,
    FieldType.TEXTAREA: _text,
    FieldType.HTML: _text,
    FieldType.MARKDOWN: _text,
    FieldType.NUMBER: _number,
    FieldType.BOOLEAN: _boolean,
    FieldType.DATE: _date,
    FieldType.DATETIME: _datetime,
    FieldType.EMAIL: _email,
    FieldType.PHONE: _phone,
    FieldType.URL: _url,
    FieldType.COLOR: _color,
    FieldType.LINK: _link,
    FieldType.IMAGE: _media,
    FieldType.VIDEO: _media,
    FieldType.AUDIO: _media,
    FieldType.FILE: _media,
    FieldType.PAGE: _page,
    FieldType.CONTACT: _contact,
    FieldType.JSON: _json,
    # SELECT has no entry: it is the one type whose validity depends on the
    # field it is on rather than on the value alone, so :func:`normalize_value`
    # builds its normaliser from the options it was handed.
}


def normalize_options(options: Any) -> list[dict[str, Any]]:
    """Validate a ``SELECT`` field's choices into ``[{"value": ..., "label": ...}]``.

    A bare list of strings is accepted and expanded, because that is what
    somebody types when the value and the label are the same word -- which they
    usually are, right up until the site adds a second language and the label
    has to change without the stored value changing under every page that used
    it.
    """
    if options in (None, "", [], {}):
        return []
    if not isinstance(options, Sequence) or isinstance(options, str | bytes):
        raise ValidationError("Expected a list of choices.")

    normalised: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, option in enumerate(options):
        if isinstance(option, str):
            option = {"value": option, "label": option}
        if not isinstance(option, Mapping) or "value" not in option:
            raise ValidationError(
                f"Choice {index + 1}: expected a word, or an object with a value."
            )
        unknown = set(option) - {"value", "label"}
        if unknown:
            raise ValidationError(
                f"Choice {index + 1}: unknown keys {', '.join(sorted(unknown))}. "
                "A choice has a value and a label."
            )
        value = option["value"]
        if not isinstance(value, str) or not value.strip():
            raise ValidationError(f"Choice {index + 1}: value must be a non-empty word.")
        value = value.strip()
        if value in seen:
            raise ValidationError(f"Choice {index + 1}: {value} is listed twice.")
        seen.add(value)
        label = option.get("label") or value
        if not isinstance(label, str):
            raise ValidationError(f"Choice {index + 1}: label must be text.")
        normalised.append({"value": value, "label": label})
    return normalised


def option_values(options: Any) -> list[str]:
    """Just the stored halves of a ``SELECT`` field's choices."""
    return [option["value"] for option in normalize_options(options)]


def _choice(options: Any) -> Callable[[Any], str]:
    """A normaliser that accepts one of this field's own choices and nothing else."""
    allowed = option_values(options)

    def normalise(value: Any) -> str:
        chosen = _text(value).strip()
        if not allowed:
            raise ValidationError(
                "This choice field has no options yet. Add them to the field first."
            )
        if chosen not in allowed:
            raise ValidationError(f"Expected one of: {', '.join(allowed)}.")
        return chosen

    return normalise


def normaliser_for(field_type: Any, options: Any = None) -> Callable[[Any], Any]:
    """The function that turns one written value of this type into its stored form."""
    if field_type in CHOICE_TYPES:
        return _choice(options)
    try:
        return NORMALISERS[field_type]
    except KeyError as error:
        raise ValidationError(f"Unknown field type: {field_type}") from error


def normalize_value(
    field_type: Any, value: Any, *, multiple: bool = False, options: Any = None
) -> Any:
    """Return the canonical form of one value, or raise :class:`ValidationError`.

    A list field validates item by item and says which item failed, because
    "Expected a URL" is unhelpful advice about the fourth of nine images.
    """
    normalise = normaliser_for(field_type, options)

    if not multiple:
        return normalise(value)

    if not isinstance(value, list):
        raise ValidationError("Expected a list of values.")
    normalised = []
    for index, item in enumerate(value):
        try:
            normalised.append(normalise(item))
        except ValidationError as error:
            raise ValidationError(f"Item {index + 1}: {'; '.join(error.messages)}") from error
    return normalised


def is_empty(value: Any) -> bool:
    """Whether a value counts as "nothing written here yet".

    ``False`` and ``0`` are content; an empty string, list or object is not.
    """
    if isinstance(value, bool | int | float):
        return False
    return value in (None, "", [], {})


def value_url(value: Any) -> str:
    """The address one media or link value points at, or ``""``.

    Used by anything that has a value but does not know its type: the admin's
    thumbnail, and the export command's audit of what a page is pulling in.
    """
    if isinstance(value, str):
        return value
    if isinstance(value, Mapping):
        url = value.get("url")
        return url if isinstance(url, str) else ""
    return ""
