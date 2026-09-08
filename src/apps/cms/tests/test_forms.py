"""The content screen: one input per type, and what comes back out of it.

This is the file that holds the app's central promise to an editor. A type is
only worth having if it changes what you type into, so every type is asserted
against the widget it earns -- a text area is a text area, a choice is a
dropdown over that field's own options, a colour opens a colour picker, an image
is an upload button *and* a URL box, and a list of images is a multi-file input.

The other half is the round trip: whatever is typed comes back as the canonical
shape :mod:`apps.cms.fields` promises, in the language that was being edited, and
without quietly losing what was written in the others.
"""

from pathlib import Path
from typing import Any

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import override_settings

from apps.cms import forms as cms_forms
from apps.cms.fields import FieldType
from apps.cms.forms import ContentForm, SiteSettingsForm, field_key, form_fields_for
from apps.cms.models import Field, Page, Section, SiteSettings
from apps.cms.theme import (
    BooleanInput,
    ColorInput,
    DateInput,
    EmailInput,
    MultiFileInput,
    NumberInput,
    Select,
    SelectMultiple,
    TelInput,
    Textarea,
    TextInput,
    URLInput,
)

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def media(tmp_path: Path) -> Any:
    """Uploads go somewhere temporary, not into the project's own media folder."""
    with override_settings(MEDIA_ROOT=tmp_path, MEDIA_URL="/media/"):
        yield tmp_path


@pytest.fixture
def section(db: None) -> Section:
    page = Page.objects.create(name="Home", slug="home")
    return Section.objects.create(page=page, name="Hero", slug="hero")


def _field(section: Section, **kwargs: Any) -> Field:
    defaults: dict[str, Any] = {
        "name": "F",
        "slug": f"f{Field.objects.count()}",
        "field_type": FieldType.TEXT,
    }
    return Field.objects.create(section=section, **{**defaults, **kwargs})


def _widgets(field: Field, value: Any = None) -> dict[str, Any]:
    """``{suffix: widget}`` for one field, where ``""`` is the field's main input."""
    key = field_key(field)
    return {
        name.removeprefix(key).removeprefix("__"): form_field.widget
        for name, (form_field, _) in form_fields_for(field, value).items()
    }


def _post(section: Section, data: dict[str, Any], files: Any = None) -> ContentForm:
    form = ContentForm(data, files, sections=[section], language="en-us")
    form.is_valid()
    return form


class TestOneWidgetPerType:
    """The whole reason a type exists: it changes what an editor types into."""

    @pytest.mark.parametrize(
        ("field_type", "widget"),
        [
            (FieldType.TEXT, TextInput),
            (FieldType.TEXTAREA, Textarea),
            (FieldType.HTML, Textarea),
            (FieldType.MARKDOWN, Textarea),
            (FieldType.NUMBER, NumberInput),
            (FieldType.BOOLEAN, BooleanInput),
            (FieldType.DATE, DateInput),
            (FieldType.EMAIL, EmailInput),
            (FieldType.PHONE, TelInput),
            (FieldType.URL, URLInput),
            (FieldType.COLOR, ColorInput),
            (FieldType.JSON, Textarea),
            (FieldType.CONTACT, Textarea),
        ],
    )
    def test_a_type_gets_its_own_input(
        self, section: Section, field_type: FieldType, widget: type
    ) -> None:
        field = _field(section, field_type=field_type)

        assert isinstance(_widgets(field)[""], widget)

    def test_a_long_type_gets_more_rows_than_a_short_one(self, section: Section) -> None:
        """A text area, an HTML box and a Markdown box are not the same size."""
        area = _widgets(_field(section, field_type=FieldType.TEXTAREA))[""]
        html = _widgets(_field(section, field_type=FieldType.HTML))[""]

        assert html.attrs["rows"] > area.attrs["rows"]

    def test_html_and_markdown_are_monospaced(self, section: Section) -> None:
        widget = _widgets(_field(section, field_type=FieldType.MARKDOWN))[""]

        assert "font-mono" in widget.attrs["class"]

    def test_a_phone_box_opens_a_keypad(self, section: Section) -> None:
        widget = _widgets(_field(section, field_type=FieldType.PHONE))[""]

        assert widget.input_type == "tel"


class TestChoiceFields:
    def test_a_choice_field_is_a_dropdown_over_its_own_options(self, section: Section) -> None:
        field = _field(section, field_type=FieldType.SELECT, options=["small", "large"])

        form_field, _ = next(iter(form_fields_for(field, None).values()))

        assert isinstance(form_field.widget, Select)
        assert [value for value, _ in form_field.choices] == ["", "small", "large"]

    def test_a_required_choice_has_no_blank_row(self, section: Section) -> None:
        field = _field(section, field_type=FieldType.SELECT, options=["small"], required=True)

        form_field, _ = next(iter(form_fields_for(field, None, required=True).values()))

        assert [value for value, _ in form_field.choices] == ["small"]

    def test_a_label_may_differ_from_the_stored_word(self, section: Section) -> None:
        field = _field(
            section,
            field_type=FieldType.SELECT,
            options=[{"value": "sm", "label": "Small"}],
        )

        form_field, _ = next(iter(form_fields_for(field, None).values()))

        assert ("sm", "Small") in list(form_field.choices)

    def test_a_list_of_choices_is_a_multiple_select(self, section: Section) -> None:
        field = _field(section, field_type=FieldType.SELECT, options=["a", "b"], multiple=True)

        assert isinstance(_widgets(field)[""], SelectMultiple)

    def test_picking_two_stores_two(self, section: Section) -> None:
        field = _field(section, field_type=FieldType.SELECT, options=["a", "b"], multiple=True)

        _post(section, {field_key(field): ["a", "b"]}).save()

        field.refresh_from_db()
        assert field.values == {"en-us": ["a", "b"]}

    def test_a_word_that_is_not_on_the_list_is_refused(self, section: Section) -> None:
        field = _field(section, field_type=FieldType.SELECT, options=["a"])

        form = _post(section, {field_key(field): "z"})

        assert field_key(field) in form.errors


class TestPageReferences:
    def test_it_is_a_dropdown_of_this_site_s_pages(self, section: Section) -> None:
        Page.objects.create(name="About us", slug="about-us")
        field = _field(section, field_type=FieldType.PAGE)

        form_field, _ = next(iter(form_fields_for(field, None).values()))

        assert ("about-us", "About us (/about-us)") in list(form_field.choices)

    def test_picking_one_stores_its_slug_rather_than_its_address(self, section: Section) -> None:
        Page.objects.create(name="About us", slug="about-us")
        field = _field(section, field_type=FieldType.PAGE)

        _post(section, {field_key(field): "about-us"}).save()

        field.refresh_from_db()
        assert field.values == {"en-us": "about-us"}


class TestMedia:
    """An image field is an upload button, an address box, a title and alt text."""

    def test_an_image_field_offers_an_upload_and_a_url(self, section: Section) -> None:
        field = _field(section, field_type=FieldType.IMAGE)

        widgets = _widgets(field)

        assert set(widgets) == {"upload", "", "title", "alt"}
        assert isinstance(widgets[""], URLInput)

    def test_the_upload_button_only_offers_what_the_type_takes(self, section: Section) -> None:
        widgets = _widgets(_field(section, field_type=FieldType.IMAGE))

        assert ".png" in widgets["upload"].attrs["accept"]
        assert ".mp4" not in widgets["upload"].attrs["accept"]

    def test_a_link_has_a_title_but_no_alt_text_and_no_upload(self, section: Section) -> None:
        """A link points at somebody else's page; there is nothing to upload."""
        assert set(_widgets(_field(section, field_type=FieldType.LINK))) == {"", "title"}

    def test_a_pasted_address_is_stored_as_the_canonical_object(self, section: Section) -> None:
        field = _field(section, field_type=FieldType.IMAGE)
        key = field_key(field)

        _post(
            section,
            {key: "https://cdn.example.com/a.png", f"{key}__alt": "A logo"},
        ).save()

        field.refresh_from_db()
        assert field.values["en-us"]["url"] == "https://cdn.example.com/a.png"
        assert field.values["en-us"]["alt"] == "A logo"

    def test_an_uploaded_file_becomes_the_address(self, section: Section) -> None:
        field = _field(section, field_type=FieldType.IMAGE)
        key = field_key(field)

        form = _post(section, {key: ""}, {f"{key}__upload": SimpleUploadedFile("hero.png", b"x")})
        form.save()

        field.refresh_from_db()
        assert field.values["en-us"]["url"].startswith("/media/cms/uploads/image/hero-")

    def test_an_upload_beats_the_address_that_was_already_there(self, section: Section) -> None:
        """Somebody who picked a file meant the file, whatever the box still says."""
        field = _field(section, field_type=FieldType.IMAGE)
        key = field_key(field)

        form = _post(
            section,
            {key: "https://cdn.example.com/old.png"},
            {f"{key}__upload": SimpleUploadedFile("new.png", b"x")},
        )
        form.save()

        field.refresh_from_db()
        assert "new-" in field.values["en-us"]["url"]

    def test_a_file_the_type_refuses_is_reported_against_the_upload_box(
        self, section: Section
    ) -> None:
        field = _field(section, field_type=FieldType.IMAGE)
        key = field_key(field)

        form = _post(section, {key: ""}, {f"{key}__upload": SimpleUploadedFile("x.exe", b"x")})

        assert f"{key}__upload" in form.errors

    def test_meta_nobody_typed_on_this_screen_survives_a_save(self, section: Section) -> None:
        """An importer's width and height must not vanish because a title changed."""
        field = _field(
            section,
            field_type=FieldType.IMAGE,
            values={
                "en-us": {
                    "url": "https://cdn.example.com/a.png",
                    "title": None,
                    "alt": None,
                    "meta": {"width": 1200},
                }
            },
        )
        key = field_key(field)

        _post(section, {key: "https://cdn.example.com/a.png", f"{key}__title": "Hero"}).save()

        field.refresh_from_db()
        assert field.values["en-us"]["meta"] == {"width": 1200}
        assert field.values["en-us"]["title"] == "Hero"


class TestListsOfMedia:
    def test_a_list_of_images_offers_a_multi_file_input(self, section: Section) -> None:
        field = _field(section, field_type=FieldType.IMAGE, multiple=True)

        widgets = _widgets(field)

        assert isinstance(widgets["upload"], MultiFileInput)
        assert widgets["upload"].attrs["multiple"] is True

    def test_the_addresses_are_one_per_line(self, section: Section) -> None:
        field = _field(
            section,
            field_type=FieldType.IMAGE,
            multiple=True,
            values={"en-us": [{"url": "https://cdn.example.com/a.png"}]},
        )

        _, initial = form_fields_for(field, field.values["en-us"])[field_key(field)]

        assert initial == "https://cdn.example.com/a.png"

    def test_several_files_are_added_to_the_list(self, section: Section) -> None:
        field = _field(section, field_type=FieldType.IMAGE, multiple=True)
        key = field_key(field)

        form = ContentForm(
            {key: "https://cdn.example.com/a.png"},
            _MultiValueFiles(
                {
                    f"{key}__upload": [
                        SimpleUploadedFile("one.png", b"x"),
                        SimpleUploadedFile("two.png", b"x"),
                    ]
                }
            ),
            sections=[section],
            language="en-us",
        )
        assert form.is_valid(), form.errors
        form.save()

        field.refresh_from_db()
        urls = [item["url"] for item in field.values["en-us"]]
        assert len(urls) == 3
        assert urls[0] == "https://cdn.example.com/a.png"

    def test_reordering_the_lines_keeps_each_picture_s_alt_text(self, section: Section) -> None:
        """The title is written beside the address, so moving the address moves it."""
        field = _field(
            section,
            field_type=FieldType.IMAGE,
            multiple=True,
            values={
                "en-us": [
                    {"url": "https://cdn.example.com/a.png", "alt": "A", "meta": {}},
                    {"url": "https://cdn.example.com/b.png", "alt": "B", "meta": {}},
                ]
            },
        )
        key = field_key(field)

        _post(
            section,
            {key: "https://cdn.example.com/b.png\nhttps://cdn.example.com/a.png"},
        ).save()

        field.refresh_from_db()
        assert [item["alt"] for item in field.values["en-us"]] == ["B", "A"]

    def test_blank_lines_are_not_items(self, section: Section) -> None:
        field = _field(section, field_type=FieldType.URL, multiple=True)
        key = field_key(field)

        _post(section, {key: "https://a.example.com\n\n  \nhttps://b.example.com"}).save()

        field.refresh_from_db()
        assert field.values["en-us"] == ["https://a.example.com", "https://b.example.com"]

    def test_a_list_with_no_line_shape_keeps_the_json_box(self, section: Section) -> None:
        """A list of dates is punctuation-delimited; a line would not say where one ends."""
        field = _field(section, field_type=FieldType.DATE, multiple=True)

        assert isinstance(_widgets(field)[""], Textarea)


class TestLanguages:
    def test_a_form_is_prefilled_from_its_own_language_only(self, section: Section) -> None:
        """Prefilling French with English would make a blind save a false translation."""
        field = _field(section, values={"en-us": "Welcome", "fa": "خوش آمدید"})

        form = ContentForm(sections=[section], language="fa")

        assert form.initial[field_key(field)] == "خوش آمدید"

    def test_saving_one_language_leaves_the_others_alone(self, section: Section) -> None:
        field = _field(section, values={"en-us": "Welcome", "fa": "خوش آمدید"})

        _post(section, {field_key(field): "Hello"}).save()

        field.refresh_from_db()
        assert field.values == {"en-us": "Hello", "fa": "خوش آمدید"}

    def test_an_emptied_box_removes_that_language_rather_than_storing_blank(
        self, section: Section
    ) -> None:
        field = _field(section, values={"en-us": "Welcome", "fa": "خوش آمدید"})

        _post(section, {field_key(field): ""}).save()

        field.refresh_from_db()
        assert field.values == {"fa": "خوش آمدید"}

    def test_a_required_field_is_insisted_on_in_the_default_language(
        self, section: Section
    ) -> None:
        field = _field(section, required=True)

        form = _post(section, {field_key(field): ""})

        assert field_key(field) in form.errors

    def test_and_not_in_the_others(self, section: Section) -> None:
        """A translation that has not been written yet is a normal state, not an error."""
        field = _field(section, required=True)

        form = ContentForm({field_key(field): ""}, sections=[section], language="fa")

        assert form.is_valid(), form.errors

    def test_a_required_image_may_be_answered_by_either_input(self, section: Section) -> None:
        field = _field(section, field_type=FieldType.IMAGE, required=True)
        key = field_key(field)

        form = _post(section, {key: ""}, {f"{key}__upload": SimpleUploadedFile("a.png", b"x")})

        assert form.is_valid(), form.errors


class TestTheFormItself:
    def test_it_knows_whether_it_needs_a_multipart_post(self, section: Section) -> None:
        """Without the enctype the page saves, says so, and drops every file."""
        _field(section, field_type=FieldType.IMAGE)

        assert ContentForm(sections=[section], language="en-us").has_uploads is True

    def test_a_page_of_text_needs_no_multipart_post(self, section: Section) -> None:
        _field(section)

        assert ContentForm(sections=[section], language="en-us").has_uploads is False

    def test_it_reports_how_many_fields_changed(self, section: Section) -> None:
        first = _field(section, values={"en-us": "One"})
        _field(section, values={"en-us": "Two"})

        changed = _post(
            section, {field_key(first): "Changed", field_key(_second(section)): "Two"}
        ).save()

        assert changed == 1

    def test_a_hidden_field_is_still_editable(self, section: Section) -> None:
        """Whoever switched it off has to be able to see what is in it."""
        field = _field(section, is_active=False, values={"en-us": "Hidden"})

        assert field_key(field) in ContentForm(sections=[section], language="en-us").fields


class TestSiteSettingsForm:
    def test_it_offers_open_graph_copy_per_language(self) -> None:
        names = cms_forms.site_translation_fields()

        assert "og_title__en-us" in names
        assert "og_description__fa" in names

    def test_meta_keywords_are_gone(self) -> None:
        """Nothing has read them for over a decade; the box only cost editors time."""
        assert not any(name.startswith("keywords") for name in cms_forms.site_translation_fields())

    def test_the_descriptions_get_room_to_write_a_sentence(self) -> None:
        fields = cms_forms.site_translation_fields()

        assert isinstance(fields["og_description__en-us"].widget, Textarea)
        assert isinstance(fields["name__en-us"].widget, TextInput)

    def test_it_writes_each_language_into_the_json_column(self) -> None:
        form_class = type("Bound", (SiteSettingsForm,), cms_forms.site_translation_fields())
        form = form_class(
            {
                "name__en-us": "Acme",
                "name__fa": "آکمی",
                "og_title__en-us": "Acme, we make things",
                "og_image": "https://cdn.example.com/card.png",
                "contact": "{}",
                "social_links": "[]",
                "extra": "{}",
            },
            instance=SiteSettings.load(),
        )

        assert form.is_valid(), form.errors
        site = form.save()
        assert site.name == {"en-us": "Acme", "fa": "آکمی"}
        assert site.og_title == {"en-us": "Acme, we make things"}


def _second(section: Section) -> Field:
    return list(section.fields.order_by("slug"))[1]


class _MultiValueFiles(dict):
    """The bit of ``MultiValueDict`` a multi-file widget actually asks for."""

    def getlist(self, key: str) -> list[Any]:
        return list(self.get(key) or [])
