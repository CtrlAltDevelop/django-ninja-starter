"""The widgets an editor actually types into, chosen from a field's type.

The structure admin is ordinary Django: superusers add pages, sections and
fields the way they add anything else. This module is for the other screen --
the one where somebody who never opens the structure admin fills a page in, one
language at a time -- and its whole job is that every type gets the input it
deserves. A text area is a text area, a choice is a dropdown over that field's
own options, a colour is a colour picker, a phone number opens a keypad, a page
reference is a list of this site's pages, and an image is an upload button with
a URL box beside it.

Four shapes cover every type:

* **one input**, for the types whose value is a single scalar;
* **a small group**, for a link or a piece of media -- an upload, the address it
  ends up at, a title and, where it means something, alt text;
* **one line per item**, for a list of things that each fit on a line: URLs,
  images, page names, words. A list of pictures is a multi-file upload plus that
  box, so adding nine images is one file dialogue.
* **a JSON box**, for the shapes that are open by design -- contact details, raw
  JSON, and lists of things too structured for a line.

Uploading and linking are the same answer arrived at differently. A media field
stores an address, so :mod:`apps.cms.uploads` turns a picked file into one and
writes it into the same box a pasted URL would have gone into. An editor at a
project on S3 and an editor at a project with a CDN full of art use the same
screen without being told which they are.

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

from apps.cms import uploads
from apps.cms.fields import (
    MEDIA_TYPES,
    OPEN_TYPES,
    FieldType,
    is_empty,
    normalize_value,
    value_url,
)
from apps.cms.models import Field, Page, SiteSettings
from apps.cms.theme import (
    BooleanInput,
    ColorInput,
    DateInput,
    EmailInput,
    FileInput,
    ImageInput,
    MultiFileInput,
    NumberInput,
    Select,
    SelectMultiple,
    SplitDateTimeInput,
    TelInput,
    Textarea,
    TextInput,
    URLInput,
)
from apps.cms.translations import default_language, known_languages

#: The types edited as an address with a label beside it: media, and the link
#: that is a URL with a label rather than a resource.
ADDRESSABLE = MEDIA_TYPES | {FieldType.LINK}
#: The media types whose value carries alt text worth a box of its own. All of
#: them: a video and a downloadable file are as much in need of a description
#: for a screen reader as a picture is.
WITH_ALT = MEDIA_TYPES
#: List types whose items each fit on one line, so the list is a box of lines
#: rather than JSON. Anything not here -- numbers, dates, contact details --
#: keeps the JSON box, where the punctuation is what says where an item ends.
LINE_TYPES = frozenset(
    {
        FieldType.TEXT,
        FieldType.URL,
        FieldType.EMAIL,
        FieldType.PHONE,
        FieldType.PAGE,
        FieldType.COLOR,
        *ADDRESSABLE,
    }
)
#: How many rows the box for a long type gets. HTML earns the most: it is the
#: one type where an editor is reading structure as well as prose.
ROWS = {FieldType.TEXTAREA: 4, FieldType.MARKDOWN: 10, FieldType.HTML: 14}
#: The long types that are code as much as copy, and read better in a column.
MONOSPACED = frozenset({FieldType.HTML, FieldType.MARKDOWN})

JSON_HELP = {
    True: "A JSON list, one entry per item.",
    False: "A JSON object of names and values.",
}
LINE_HELP = "One per line. Blank lines are ignored."
UPLOAD_HELP = "Pick a file to upload, or paste an address into the box beside it."


def field_key(field: Field) -> str:
    """The form-field name for a content field. Stable, and safe in HTML."""
    return f"f{field.id.hex}"


class MultiFileField(forms.FileField):
    """A file input that takes several files and cleans them as a list.

    ``FileField`` validates one file, so each is run through the parent's own
    ``clean`` -- which is what keeps "that is not a file" and "this field is
    required" phrased the way they are everywhere else in the admin.
    """

    widget = MultiFileInput

    def clean(self, data: Any, initial: Any = None) -> list[Any]:
        items = data if isinstance(data, list | tuple) else ([data] if data else [])
        return [super(MultiFileField, self).clean(item, initial) for item in items if item]


def _page_choices() -> list[tuple[str, str]]:
    """Every page on this site, as ``(slug, name)`` for a page-reference dropdown.

    A query per page-reference field on the screen would be a query per field;
    there is rarely more than a handful, and the alternative -- typing a slug
    from memory -- is how a reference ends up pointing at a page that was
    renamed last month.
    """
    return [(slug, f"{name} (/{slug})") for slug, name in Page.objects.values_list("slug", "name")]


def _lines(value: Any) -> str:
    """A stored list rendered back into the box it was typed into."""
    if not isinstance(value, list):
        return ""
    return "\n".join(value_url(item) or str(item) for item in value)


def _split(text: Any) -> list[str]:
    """The box's lines, blank ones dropped."""
    if not isinstance(text, str):
        return []
    return [line.strip() for line in text.splitlines() if line.strip()]


def _upload_field(field: Field, *, many: bool) -> forms.Field:
    """The upload button for a media field, sized to how many it holds."""
    widget = ImageInput if field.field_type == FieldType.IMAGE else FileInput
    allowed = uploads.UPLOAD_EXTENSIONS.get(field.field_type) or ()
    attrs: dict[str, Any] = {"accept": ",".join(allowed)} if allowed else {}
    if many:
        return MultiFileField(
            required=False,
            label=f"Upload {field.name.lower()}",
            widget=MultiFileInput(attrs=attrs),
            help_text="Pick several at once. They are added to the list below.",
        )
    return forms.FileField(
        required=False, label="Upload", widget=widget(attrs=attrs), help_text=UPLOAD_HELP
    )


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
        case FieldType.TEXTAREA | FieldType.MARKDOWN | FieldType.HTML:
            # Monospaced for the two that are code as much as copy: an unclosed
            # tag is findable in a monospaced column and invisible in prose.
            attrs: dict[str, Any] = {"rows": ROWS[field.field_type]}
            if field.field_type in MONOSPACED:
                attrs["class"] = "font-mono"
            return forms.CharField(widget=Textarea(attrs=attrs), **common)
        case FieldType.SELECT:
            # The blank row is what lets an editor take an answer back out. It is
            # dropped when the field is required, where "no answer" is not one.
            choices = field.choices if required else [("", "---------"), *field.choices]
            return forms.ChoiceField(choices=choices, widget=Select(), **common)
        case FieldType.PAGE:
            pages = _page_choices()
            return forms.ChoiceField(
                choices=pages if required else [("", "---------"), *pages],
                widget=Select(),
                help_text="A page on this site. Renaming its slug moves this with it.",
                **common,
            )
        case FieldType.NUMBER:
            return forms.FloatField(widget=NumberInput(attrs={"step": "any"}), **common)
        case FieldType.BOOLEAN:
            # Never required: an unticked switch is an answer, not a blank.
            return forms.BooleanField(**{**common, "required": False, "widget": BooleanInput()})
        case FieldType.DATE:
            return forms.DateField(widget=DateInput(), **common)
        case FieldType.DATETIME:
            return forms.SplitDateTimeField(widget=SplitDateTimeInput(), **common)
        case FieldType.EMAIL:
            return forms.EmailField(widget=EmailInput(), **common)
        case FieldType.PHONE:
            return forms.CharField(
                widget=TelInput(attrs={"placeholder": "+44 20 7946 0958"}), **common
            )
        case FieldType.URL:
            return forms.URLField(assume_scheme="https", widget=URLInput(), **common)
        case FieldType.COLOR:
            return forms.CharField(widget=ColorInput(), **common)
        case _:
            return forms.CharField(widget=TextInput(), **common)


def _json_field(field: Field, required: bool) -> forms.Field:
    return forms.JSONField(
        required=required,
        label=field.name,
        widget=Textarea(attrs={"rows": 6, "class": "font-mono"}),
        help_text=JSON_HELP[field.multiple],
    )


def _list_fields(field: Field, value: Any, required: bool) -> dict[str, tuple[forms.Field, Any]]:
    """The inputs for a field that holds several of something."""
    key = field_key(field)
    if field.field_type == FieldType.SELECT:
        return {
            key: (
                forms.MultipleChoiceField(
                    required=required,
                    label=field.name,
                    choices=field.choices,
                    widget=SelectMultiple(),
                    help_text="Hold ctrl or cmd to pick more than one.",
                ),
                value if isinstance(value, list) else [],
            )
        }
    if field.field_type not in LINE_TYPES:
        return {key: (_json_field(field, required), value)}

    parts: dict[str, tuple[forms.Field, Any]] = {}
    if field.field_type in MEDIA_TYPES:
        parts[f"{key}__upload"] = (_upload_field(field, many=True), None)
    parts[key] = (
        forms.CharField(
            required=required,
            label=field.name,
            widget=Textarea(attrs={"rows": 5, "class": "font-mono"}),
            help_text=LINE_HELP,
        ),
        _lines(value),
    )
    return parts


def form_fields_for(
    field: Field, value: Any, *, required: bool = False
) -> dict[str, tuple[forms.Field, Any]]:
    """Return ``{name: (form field, initial)}`` for one content field."""
    key = field_key(field)

    if field.multiple:
        return _list_fields(field, value, required)

    if field.field_type in OPEN_TYPES:
        return {key: (_json_field(field, required), value)}

    if field.field_type in ADDRESSABLE:
        stored = value if isinstance(value, dict) else {}
        parts: dict[str, tuple[forms.Field, Any]] = {}
        if field.field_type in MEDIA_TYPES:
            parts[f"{key}__upload"] = (_upload_field(field, many=False), None)
        parts[key] = (
            forms.URLField(
                # Never required at the widget: an upload beside it is the other
                # way of answering, and a browser refusing to submit an empty
                # box would make the upload button unreachable. The pair is
                # checked together in `value_from`.
                required=False,
                label=field.name,
                assume_scheme="https",
                max_length=500,
                widget=URLInput(),
                help_text=UPLOAD_HELP if field.field_type in MEDIA_TYPES else "",
            ),
            stored.get("url", value if isinstance(value, str) else ""),
        )
        parts[f"{key}__title"] = (
            forms.CharField(required=False, label="Title", max_length=200, widget=TextInput()),
            stored.get("title", ""),
        )
        if field.field_type in WITH_ALT:
            parts[f"{key}__alt"] = (
                forms.CharField(
                    required=False,
                    label="Alt text",
                    max_length=200,
                    widget=TextInput(),
                    help_text="What somebody who cannot see this would be read.",
                ),
                stored.get("alt", ""),
            )
        return parts

    return {key: (_scalar(field, required), value)}


def _stored_by_url(previous: Any) -> dict[str, dict[str, Any]]:
    """The titles, alt text and meta already written, keyed by the URL they belong to.

    A list of pictures is edited as a list of addresses, so anything the editor
    typed beside one -- or an importer put under ``meta`` -- would be lost every
    time the order changed. Matching on the address puts it back.
    """
    items = previous if isinstance(previous, list) else []
    return {value_url(item): item for item in items if isinstance(item, dict) and value_url(item)}


def value_from(field: Field, cleaned: dict[str, Any], previous: Any = None) -> Any:
    """Rebuild one content value out of the inputs that were submitted for it.

    Uploads are stored here, because this is the point at which it is known that
    a file was actually offered for this field -- and :func:`apps.cms.uploads.store`
    refuses what the type does not take before anything reaches storage.
    """
    key = field_key(field)
    submitted = cleaned.get(key)
    picked = cleaned.get(f"{key}__upload")

    if field.multiple:
        if field.field_type == FieldType.SELECT:
            return list(submitted or [])
        if field.field_type not in LINE_TYPES:
            return submitted
        addresses = _split(submitted)
        addresses.extend(uploads.store(item, field.field_type) for item in picked or [])
        if field.field_type not in ADDRESSABLE:
            return addresses
        known = _stored_by_url(previous)
        return [known.get(address, {"url": address}) for address in addresses]

    if field.field_type in OPEN_TYPES:
        return submitted

    if field.field_type in ADDRESSABLE:
        # An upload wins: somebody who picked a file this time round meant the
        # file, whatever the box beside it still says from last time.
        address = uploads.store(picked, field.field_type) if picked else submitted
        if is_empty(address):
            return None
        stored = previous if isinstance(previous, dict) else {}
        value: dict[str, Any] = {"url": address}
        # Whatever an importer or an API client put under `meta` was never on
        # this screen, so saving from this screen must not be how it disappears.
        if stored.get("meta"):
            value["meta"] = stored["meta"]
        if title := cleaned.get(f"{key}__title"):
            value["title"] = title
        if alt := cleaned.get(f"{key}__alt"):
            value["alt"] = alt
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
        #: The fields the default language insists on. Kept here rather than on
        #: the widgets because a media field's URL box is never required on its
        #: own -- the upload button beside it is the other way of answering it.
        self.insisted_on: set[Any] = set()
        for section in sections:
            for section_or_child in (section, *section.children.all()):
                for field in section_or_child.fields.all():
                    self.content_fields.append(field)
                    needed = field.required and language == default_language()
                    if needed:
                        self.insisted_on.add(field.pk)
                    for name, (form_field, initial) in form_fields_for(
                        field, field.values.get(language), required=needed
                    ).items():
                        form_field.help_text = form_field.help_text or field.help_text
                        self.fields[name] = form_field
                        self.initial.setdefault(name, initial)

    @property
    def has_uploads(self) -> bool:
        """Whether any input on this form is a file one.

        The template asks, because a form posted without
        ``enctype="multipart/form-data"`` loses its files silently -- the page
        saves, says so, and the picture is not there.
        """
        return any(isinstance(field.widget, forms.FileInput) for field in self.fields.values())

    def clean(self) -> dict[str, Any]:
        """Normalise every value with the model's own rules, and blame the right box."""
        cleaned = super().clean()
        self.values: dict[Field, Any] = {}
        for field in self.content_fields:
            previous = field.values.get(self.language)
            key = field_key(field)
            try:
                value = value_from(field, cleaned, previous)
            except ValidationError as error:
                # Raised by an upload the type or the size limit refuses, so the
                # message belongs on the upload button when there is one.
                upload = f"{key}__upload"
                self.add_error(upload if upload in self.fields else key, error)
                continue
            if is_empty(value):
                if field.pk in self.insisted_on:
                    # The widget cannot insist for us: a media field is answered
                    # by either of two inputs, and a required URL box would put
                    # the browser in the way of the upload button.
                    self.add_error(key, "This field is required.")
                self.values[field] = None
                continue
            try:
                self.values[field] = normalize_value(
                    field.field_type, value, multiple=field.multiple, options=field.options
                )
            except ValidationError as error:
                self.add_error(key, error)
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


#: The site attributes stored as ``{language: text}``, and what to call each on
#: screen. Open Graph is here rather than beside the branding URLs because it is
#: copy: the sentence under a shared link is written, not configured.
SITE_TRANSLATED = {
    "name": "Site name",
    "tagline": "Tagline",
    "description": "Meta description",
    "og_title": "Social title",
    "og_description": "Social description",
}
#: The ones an editor should be given room to write a sentence in.
SITE_LONG = frozenset({"description", "og_description"})


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
            long_form = attribute in SITE_LONG
            fields[f"{attribute}__{language}"] = forms.CharField(
                required=False,
                label=f"{label} [{language}]",
                widget=Textarea(attrs={"rows": 2}) if long_form else TextInput(),
                help_text=(
                    "Around 160 characters is what a search result and a shared card will show."
                )
                if long_form
                else "",
            )
    return fields


class SiteSettingsForm(forms.ModelForm):
    """The site metadata form, with one input per language for the copy.

    The model stores the translated ones as ``{language: value}`` objects.
    Handing an editor the raw JSON would make a punctuation error in a brace the
    difference between a working site header and a validation page, so the form
    flattens them into ordinary inputs and puts them back together on the way in.
    """

    class Meta:
        model = SiteSettings
        fields = (
            "logo",
            "favicon",
            "og_image",
            "og_url",
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
                self.initial.setdefault(name, stored.get(language) or "")

    def clean(self) -> dict[str, Any]:
        cleaned = super().clean()
        for attribute in SITE_TRANSLATED:
            collected: dict[str, str] = {}
            for language in known_languages():
                name = self.translated_names.get((attribute, language))
                if name is None:
                    continue
                if raw := (cleaned.get(name) or "").strip():
                    collected[language] = raw
            setattr(self.instance, attribute, collected)
        return cleaned
