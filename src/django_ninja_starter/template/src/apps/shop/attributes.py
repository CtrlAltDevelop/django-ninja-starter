"""The types a category's attributes come in, and the one shape each of them stores.

A category here is not just a folder. It is the *shape* of the products inside
it: a category declares which attributes its products have, of what type, which
of them are required, and which of them tell one variant of a product from
another. "Screen size, in inches, a number, required" is a fact about laptops,
not about the one laptop somebody is adding this afternoon.

That is why the types live in their own module, next to their validators, and
why every value is normalised on the way in. ``JSONField`` will store anything;
a storefront that is handed ``"14"`` on one product and ``14`` on the next has
to defend against both, forever, in every renderer. So an attribute's declared
type decides the stored shape, and a value that cannot be made to fit is refused
at the point somebody typed it rather than at the point somebody renders it.

The set is deliberately small. Anything a shop genuinely cannot express with it
belongs in the product's own description, not in a fourteenth attribute type
nobody remembers the semantics of.
"""

from collections.abc import Callable, Sequence
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from django.core.exceptions import ValidationError
from django.core.validators import URLValidator
from django.db import models
from django.utils.dateparse import parse_date

COLOR_LENGTH = {4, 7}


class AttributeType(models.TextChoices):
    """What one value of a category attribute is.

    The stored value is the member's string, so the wire format survives
    reordering this class, and a client reading ``"choice"`` needs no lookup
    table to know what it has.
    """

    TEXT = "text", "Text"
    NUMBER = "number", "Number"
    BOOLEAN = "boolean", "Yes or no"
    CHOICE = "choice", "One of a list"
    MULTI_CHOICE = "multi_choice", "Several of a list"
    COLOR = "color", "Colour"
    DATE = "date", "Date"
    URL = "url", "Link"


#: The types whose values are drawn from the attribute's own ``choices`` list.
CHOICE_TYPES = frozenset({AttributeType.CHOICE, AttributeType.MULTI_CHOICE})

#: The types that can tell one variant of a product from another. A variant is
#: picked from a dropdown -- "red, size 42" -- so the attribute has to have a
#: finite, enumerable set of values. A number or a date does not.
VARIANT_TYPES = frozenset({AttributeType.CHOICE, AttributeType.COLOR, AttributeType.TEXT})


def _text(value: Any, _choices: Sequence[str]) -> str:
    if not isinstance(value, str):
        raise ValidationError("Expected text.")
    text = value.strip()
    if not text:
        raise ValidationError("Expected text.")
    return text


def _number(value: Any, _choices: Sequence[str]) -> float | int:
    """Accept what a form posts as well as what JSON carries.

    An admin form hands this a string, an import hands it a number, and both
    mean the same thing. Integers stay integers so that ``16`` does not become
    ``16.0`` on its way to a spec table.
    """
    if isinstance(value, bool):
        raise ValidationError("Expected a number.")
    if isinstance(value, int | float):
        number = value
    else:
        if not isinstance(value, str):
            raise ValidationError("Expected a number.")
        try:
            number = float(Decimal(value.strip()))
        except (InvalidOperation, ValueError) as error:
            raise ValidationError("Expected a number.") from error
    if isinstance(number, float) and number.is_integer():
        return int(number)
    return number


def _boolean(value: Any, _choices: Sequence[str]) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str) and value.strip().lower() in {"true", "false", "yes", "no"}:
        return value.strip().lower() in {"true", "yes"}
    raise ValidationError("Expected yes or no.")


def _choice(value: Any, choices: Sequence[str]) -> str:
    text = _text(value, choices)
    if text not in choices:
        raise ValidationError(f"Not one of the allowed values: {', '.join(choices)}.")
    return text


def _multi_choice(value: Any, choices: Sequence[str]) -> list[str]:
    """A list, and stored in the order the attribute declares its choices.

    Sorting into the declared order rather than the typed order means two
    products carrying the same three options compare equal, which is what makes
    "filter by exactly these options" a database question rather than a Python
    one.
    """
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, list):
        raise ValidationError("Expected a list of values.")
    picked = {_choice(item, choices) for item in value}
    return [choice for choice in choices if choice in picked]


def _color(value: Any, choices: Sequence[str]) -> str:
    """A hex colour, upper-cased so that ``#fff`` and ``#FFF`` are one value."""
    text = _text(value, choices)
    if not text.startswith("#") or len(text) not in COLOR_LENGTH:
        raise ValidationError("Expected a hex colour such as #1a2b3c.")
    try:
        int(text[1:], 16)
    except ValueError as error:
        raise ValidationError("Expected a hex colour such as #1a2b3c.") from error
    return text.upper()


def _date(value: Any, choices: Sequence[str]) -> str:
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    text = _text(value, choices)
    if parse_date(text) is None:
        raise ValidationError("Expected a date as YYYY-MM-DD.")
    return text


def _url(value: Any, choices: Sequence[str]) -> str:
    url = _text(value, choices)
    try:
        URLValidator()(url)
    except ValidationError as error:
        raise ValidationError("Expected a URL.") from error
    return url


# Keyed and looked up by the choice member, which is a string at runtime. The
# annotation says ``Any`` because Django is untyped here, so a checker sees the
# member as the ``(value, label)`` tuple it was written as.
NORMALISERS: dict[Any, Callable[[Any, Sequence[str]], Any]] = {
    AttributeType.TEXT: _text,
    AttributeType.NUMBER: _number,
    AttributeType.BOOLEAN: _boolean,
    AttributeType.CHOICE: _choice,
    AttributeType.MULTI_CHOICE: _multi_choice,
    AttributeType.COLOR: _color,
    AttributeType.DATE: _date,
    AttributeType.URL: _url,
}


def normalize_value(attribute_type: Any, value: Any, *, choices: Sequence[str] = ()) -> Any:
    """Return the canonical form of one attribute value, or raise ``ValidationError``."""
    try:
        normalise = NORMALISERS[attribute_type]
    except KeyError as error:
        raise ValidationError(f"Unknown attribute type: {attribute_type}") from error
    return normalise(value, list(choices))


def is_empty(value: Any) -> bool:
    """Whether a value counts as "nothing filled in here".

    ``False`` and ``0`` are answers; an empty string, list or object is not.
    """
    if isinstance(value, bool | int | float):
        return False
    return value in (None, "", [], {})


def clean_choices(value: Any) -> list[str]:
    """Validate an attribute's own list of allowed values.

    Written by whoever designs the category, so the failures worth catching are
    a typo in the JSON and a duplicate -- the second of which would otherwise
    put the same option in a dropdown twice.
    """
    if value in (None, ""):
        return []
    if isinstance(value, str):
        value = [part.strip() for part in value.split(",")]
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ValidationError("Expected a list of text values.")
    cleaned = [item.strip() for item in value if item.strip()]
    if len(set(cleaned)) != len(cleaned):
        raise ValidationError("The same choice is listed twice.")
    return cleaned
