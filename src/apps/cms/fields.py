"""The field types content is made of, and the one shape each of them stores.

Two ideas do most of the work here.

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

Every value keeps a ``meta`` object for the things this app has no opinion about
-- an image's dimensions, a link's ``rel``, a tracking id. That is the escape
hatch, so the named keys can stay small and typed.
"""

from collections.abc import Callable, Mapping
from datetime import date, datetime
from typing import Any

from django.core.exceptions import ValidationError
from django.core.validators import EmailValidator, URLValidator
from django.db import models
from django.utils.dateparse import parse_date, parse_datetime

LINK_KEYS = frozenset({"url", "title", "meta"})
MEDIA_KEYS = frozenset({"url", "title", "alt", "meta"})


class FieldType(models.TextChoices):
    """What one item of a field's value is.

    The stored value is the member's string, not its position, so the wire
    format survives reordering this class -- and a client reading ``"image"``
    needs no lookup table to know what it has.
    """

    TEXT = "text", "Text"
    TEXTAREA = "textarea", "Text area"
    HTML = "html", "HTML"
    NUMBER = "number", "Number"
    BOOLEAN = "boolean", "Boolean"
    DATE = "date", "Date"
    DATETIME = "datetime", "Date and time"
    EMAIL = "email", "Email"
    LINK = "link", "Link"
    IMAGE = "image", "Image"
    VIDEO = "video", "Video"
    FILE = "file", "File"
    CONTACT = "contact", "Contact details"


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
    url = _text(value).strip()
    try:
        URLValidator()(url)
    except ValidationError as error:
        raise ValidationError("Expected a URL.") from error
    return url


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
    FieldType.NUMBER: _number,
    FieldType.BOOLEAN: _boolean,
    FieldType.DATE: _date,
    FieldType.DATETIME: _datetime,
    FieldType.EMAIL: _email,
    FieldType.LINK: _link,
    FieldType.IMAGE: _media,
    FieldType.VIDEO: _media,
    FieldType.FILE: _media,
    FieldType.CONTACT: _contact,
}


def normalize_value(field_type: Any, value: Any, *, multiple: bool = False) -> Any:
    """Return the canonical form of one value, or raise :class:`ValidationError`.

    A list field validates item by item and says which item failed, because
    "Expected a URL" is unhelpful advice about the fourth of nine images.
    """
    try:
        normalise = NORMALISERS[field_type]
    except KeyError as error:
        raise ValidationError(f"Unknown field type: {field_type}") from error

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
