"""The widgets an editor actually types into, chosen from a field's type.

The structure admin is ordinary Django: superusers add pages, sections and
fields the way they add anything else. This module is for the other screen --
the one where somebody who never opens the structure admin fills a page in, one
language at a time -- and its whole job is that a date field is a date input, an
image is a URL box with alt text next to it, and a checkbox is a checkbox.

Three shapes cover every type:

* one input, for the types whose value is a single scalar;
* a small group of inputs, for a link or a piece of media -- URL, title and,
  where it means something, alt text;
* a JSON box, for lists and for contact details, whose shape is open by design.

Whatever gets typed is normalised by :mod:`apps.cms.fields` before it is stored,
so this module is allowed to be lenient. It validates by *asking the model*
rather than by reimplementing the rules: the form assembles a candidate value,
runs the same normaliser the model would, and reports the failure against the
input it came from.
"""

from collections.abc import Iterable
from typing import Any

from django import forms
from django.core.exceptions import ValidationError

from apps.cms.fields import FieldType, is_empty, normalize_value
from apps.cms.models import Field, SiteSettings
from apps.cms.theme import (
    BooleanInput,
    DateInput,
    EmailInput,
    SplitDateTimeInput,
    Textarea,
    TextInput,
    URLInput,
)
from apps.cms.translations import default_language, known_languages

ADDRESSABLE = frozenset({FieldType.LINK, FieldType.IMAGE, FieldType.VIDEO, FieldType.FILE})
WITH_ALT = frozenset({FieldType.IMAGE, FieldType.VIDEO, FieldType.FILE})
JSON_HELP = {
    True: "A JSON list, one entry per item.",
    False: "A JSON object of names and values.",
}


def field_key(field: Field) -> str:
    """The form-field name for a content field. Stable, and safe in HTML."""
    return f"f{field.id.hex}"


def _scalar(field: Field, required: bool) -> forms.Field:
    """One input, styled the way the rest of the admin styles that input.

    The widgets come from :mod:`apps.cms.theme`, which is the admin theme's
    where the project has one: a date here opens the same picker a date on a
    change form opens, and a boolean is the same switch. Anything else would
    make this screen look like a page from a different application, which is
    exactly what it is trying not to be.
    """
    common: dict[str, Any] = {"required": required, "label": field.name}
    match field.field_type:
        case FieldType.TEXTAREA:
            return forms.CharField(widget=Textarea(attrs={"rows": 4}), **common)
        case FieldType.HTML:
            return forms.CharField(widget=Textarea(attrs={"rows": 12}), **common)
        case FieldType.NUMBER:
            return forms.FloatField(widget=TextInput(), **common)
        case FieldType.BOOLEAN:
            # Never required: an unticked switch is an answer, not a blank.
            return forms.BooleanField(**{**common, "required": False, "widget": BooleanInput()})
        case FieldType.DATE:
            return forms.DateField(widget=DateInput(), **common)
        case FieldType.DATETIME:
            return forms.SplitDateTimeField(widget=SplitDateTimeInput(), **common)
        case FieldType.EMAIL:
            return forms.EmailField(widget=EmailInput(), **common)
        case _:
            return forms.CharField(widget=TextInput(), **common)


def form_fields_for(
    field: Field, value: Any, *, required: bool = False
) -> dict[str, tuple[forms.Field, Any]]:
    """Return ``{name: (form field, initial)}`` for one content field."""
    key = field_key(field)

    if field.multiple or field.field_type == FieldType.CONTACT:
        json_field = forms.JSONField(
            required=required,
            label=field.name,
            widget=Textarea(attrs={"rows": 6}),
            help_text=JSON_HELP[field.multiple],
        )
        return {key: (json_field, value)}

    if field.field_type in ADDRESSABLE:
        stored = value if isinstance(value, dict) else {}
        parts = {
            key: (
                forms.URLField(
                    required=required,
                    label=field.name,
                    assume_scheme="https",
                    max_length=500,
                    widget=URLInput(),
                ),
                stored.get("url", value if isinstance(value, str) else ""),
            ),
            f"{key}__title": (
                forms.CharField(
                    required=False,
                    label="Title",
                    max_length=200,
                    widget=TextInput(),
                ),
                stored.get("title", ""),
            ),
        }
        if field.field_type in WITH_ALT:
            parts[f"{key}__alt"] = (
                forms.CharField(
                    required=False,
                    label="Alt text",
                    max_length=200,
                    widget=TextInput(),
                ),
                stored.get("alt", ""),
            )
        return parts

    return {key: (_scalar(field, required), value)}


def value_from(field: Field, cleaned: dict[str, Any]) -> Any:
    """Rebuild one content value out of the inputs that were submitted for it."""
    key = field_key(field)
    submitted = cleaned.get(key)

    if field.multiple or field.field_type == FieldType.CONTACT:
        return submitted

    if field.field_type in ADDRESSABLE:
        if is_empty(submitted):
            return None
        value: dict[str, Any] = {"url": submitted}
        if title := cleaned.get(f"{key}__title"):
            value["title"] = title
        if alt := cleaned.get(f"{key}__alt"):
            value["alt"] = alt
        # Anything the editor put under meta was set elsewhere; keep it.
        return value

    return submitted


class ContentForm(forms.Form):
    """Every field of some sections, in one language.

    Built at runtime from whatever sections it is handed, because that structure
    is data: a form class written in advance could only describe the pages that
    existed when it was written. The page screen hands it a page's own sections
    interleaved with the library sections placed on it; the library screen hands
    it one shared section.

    Initial values are read from the requested language *exactly* -- never
    through the fallback. Prefilling a French form with the English text would
    mean an editor who saves without touching a box has just declared the
    English copy to be the French translation.
    """

    def __init__(self, *args: Any, sections: Iterable[Any], language: str, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.language = language
        self.content_fields: list[Field] = []
        for section in sections:
            for section_or_child in (section, *section.children.all()):
                for field in section_or_child.fields.all():
                    self.content_fields.append(field)
                    needed = field.required and language == default_language()
                    for name, (form_field, initial) in form_fields_for(
                        field, field.values.get(language), required=needed
                    ).items():
                        form_field.help_text = form_field.help_text or field.help_text
                        self.fields[name] = form_field
                        self.initial.setdefault(name, initial)

    def clean(self) -> dict[str, Any]:
        """Normalise every value with the model's own rules, and blame the right box."""
        cleaned = super().clean()
        self.values: dict[Field, Any] = {}
        for field in self.content_fields:
            value = value_from(field, cleaned)
            if is_empty(value):
                self.values[field] = None
                continue
            try:
                self.values[field] = normalize_value(
                    field.field_type, value, multiple=field.multiple
                )
            except ValidationError as error:
                self.add_error(field_key(field), error)
        return cleaned

    def save(self) -> int:
        """Write this language's values, and return how many fields changed.

        An emptied box removes that language rather than storing ``""``: the
        difference matters on read, where a missing translation falls back to
        one that exists and an empty string would render as a blank heading.
        """
        changed = 0
        for field, value in self.values.items():
            values = dict(field.values)
            if value is None:
                values.pop(self.language, None)
            else:
                values[self.language] = value
            if values != field.values:
                field.values = values
                field.save(update_fields=("values", "updated_at"))
                changed += 1
        return changed


SITE_TRANSLATED = {
    "name": "Site name",
    "tagline": "Tagline",
    "description": "Meta description",
    "keywords": "Meta keywords",
}


def site_translation_fields() -> dict[str, forms.Field]:
    """One input per translated attribute per language, named ``attribute__language``.

    Built as a function rather than written on the form class because the
    languages come from settings: a form class fixes its fields at import time,
    and this project's language list is a deployment's choice, changed without
    touching code and overridden in tests.

    The admin turns these into declared fields on a per-request subclass, which
    is what makes them appear in fieldsets at all -- a field added in ``__init__``
    is missing from ``base_fields``, and the admin builds its layout from
    ``base_fields``. That is a quiet failure: the form works, and half of it is
    invisible.
    """
    fields: dict[str, forms.Field] = {}
    for attribute, label in SITE_TRANSLATED.items():
        for language in known_languages():
            fields[f"{attribute}__{language}"] = forms.CharField(
                required=False,
                label=f"{label} [{language}]",
                widget=TextInput(),
                help_text="Comma separated." if attribute == "keywords" else "",
            )
    return fields


class SiteSettingsForm(forms.ModelForm):
    """The site metadata form, with one input per language for the copy.

    The model stores those four as ``{language: value}`` objects. Handing an
    editor the raw JSON would make a punctuation error in a brace the difference
    between a working site header and a validation page, so the form flattens
    them into ordinary inputs and puts them back together on the way in.
    """

    class Meta:
        model = SiteSettings
        fields = (
            "logo",
            "favicon",
            "og_image",
            "contact",
            "social_links",
            "extra",
        )

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.translated_names: dict[tuple[str, str], str] = {}
        for attribute in SITE_TRANSLATED:
            stored = getattr(self.instance, attribute, None) or {}
            for language in known_languages():
                name = f"{attribute}__{language}"
                if name not in self.fields:
                    continue
                self.translated_names[attribute, language] = name
                initial = stored.get(language)
                is_words = attribute == "keywords"
                self.initial.setdefault(
                    name, ", ".join(initial) if is_words and initial else (initial or "")
                )

    def clean(self) -> dict[str, Any]:
        cleaned = super().clean()
        for attribute in SITE_TRANSLATED:
            collected: dict[str, Any] = {}
            for language in known_languages():
                name = self.translated_names.get((attribute, language))
                if name is None:
                    continue
                raw = (cleaned.get(name) or "").strip()
                if not raw:
                    continue
                collected[language] = (
                    [word.strip() for word in raw.split(",") if word.strip()]
                    if attribute == "keywords"
                    else raw
                )
            setattr(self.instance, attribute, collected)
        return cleaned
