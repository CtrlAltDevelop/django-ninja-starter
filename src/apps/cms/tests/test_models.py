"""What the content models accept, and what they refuse.

The refusals are the interesting half: a CMS whose JSON column takes anything is
a frontend crash waiting for the right page to be opened.
"""

from datetime import date

import pytest
from django.core.exceptions import ValidationError
from django.test import override_settings

from apps.cms.fields import FieldType, is_empty, normalize_value
from apps.cms.models import Field, Page, Section, SiteSettings

pytestmark = pytest.mark.django_db


def _field(page: Page, **kwargs: object) -> Field:
    section = Section.objects.create(page=page, name="S", slug=f"s-{Section.objects.count()}")
    defaults = {"name": "F", "slug": "f", "field_type": FieldType.TEXT}
    return Field.objects.create(section=section, **{**defaults, **kwargs})


class TestNormalisation:
    """Whatever is typed, one shape comes out."""

    def test_a_bare_url_becomes_the_full_image_object(self) -> None:
        value = normalize_value(FieldType.IMAGE, "https://cdn.example.com/a.jpg")

        assert value == {
            "url": "https://cdn.example.com/a.jpg",
            "title": None,
            "alt": None,
            "meta": {},
        }

    def test_a_link_keeps_its_title_and_meta(self) -> None:
        value = normalize_value(
            FieldType.LINK,
            {"url": "https://example.com", "title": "Home", "meta": {"rel": "nofollow"}},
        )

        assert value == {"url": "https://example.com", "title": "Home", "meta": {"rel": "nofollow"}}

    def test_a_date_object_is_stored_as_an_iso_string(self) -> None:
        assert normalize_value(FieldType.DATE, date(2026, 9, 1)) == "2026-09-01"

    def test_a_list_field_says_which_item_was_wrong(self) -> None:
        with pytest.raises(ValidationError) as failure:
            normalize_value(
                FieldType.IMAGE,
                ["https://cdn.example.com/a.jpg", "not a url"],
                multiple=True,
            )

        assert "Item 2" in failure.value.messages[0]

    def test_a_misspelled_key_is_refused_rather_than_stored(self) -> None:
        with pytest.raises(ValidationError, match="Unknown keys: titel"):
            normalize_value(FieldType.LINK, {"url": "https://example.com", "titel": "Home"})

    def test_booleans_are_not_numbers(self) -> None:
        with pytest.raises(ValidationError):
            normalize_value(FieldType.NUMBER, True)

    def test_false_and_zero_are_content(self) -> None:
        assert not is_empty(False)
        assert not is_empty(0)
        assert is_empty("")
        assert is_empty([])


class TestFieldValues:
    def test_values_are_stored_canonically(self, home: Page) -> None:
        field = Field.objects.get(slug="background")

        assert field.values["en-us"]["url"] == "https://cdn.example.com/hero.jpg"

    def test_an_unconfigured_language_is_refused(self, home: Page) -> None:
        field = Field.objects.get(slug="headline")
        field.values = {"de": "Willkommen"}

        with pytest.raises(ValidationError, match="Not a configured language: de"):
            field.save()

    def test_a_value_of_the_wrong_type_names_its_language(self, home: Page) -> None:
        field = Field.objects.get(slug="headline")
        field.values = {"en-us": "Welcome", "fa": 12}

        with pytest.raises(ValidationError, match="fa:"):
            field.save()

    def test_a_required_field_can_exist_before_anybody_has_written_it(self, home: Page) -> None:
        field = _field(home, required=True)

        assert field.pk is not None
        assert field.is_complete is False

    def test_a_widening_type_change_keeps_the_content(self, home: Page) -> None:
        field = Field.objects.get(slug="headline")
        field.field_type = FieldType.TEXTAREA
        field.save()

        assert field.value_for("en-us") == "Welcome"

    def test_an_incompatible_type_change_is_refused_instead_of_clearing(self, home: Page) -> None:
        field = Field.objects.get(slug="headline")
        field.field_type = FieldType.NUMBER

        with pytest.raises(ValidationError, match="Expected a number"):
            field.save()

    def test_reading_a_missing_language_falls_back(self, home: Page) -> None:
        field = Field.objects.get(slug="background")

        assert field.value_for("fa")["url"] == "https://cdn.example.com/hero.jpg"

    def test_two_fields_in_one_section_cannot_share_a_slug(self, home: Page) -> None:
        hero = Section.objects.get(slug="hero")

        with pytest.raises(ValidationError):
            Field.objects.create(section=hero, name="Other", slug="headline")


class TestSections:
    def test_nesting_stops_at_one_level(self, home: Page) -> None:
        child = Section.objects.get(slug="basic")

        with pytest.raises(ValidationError, match="one level deep"):
            Section.objects.create(page=home, parent=child, name="Deep", slug="deep")

    def test_a_parent_on_another_page_is_refused(self, home: Page) -> None:
        other = Page.objects.create(name="About", slug="about")
        hero = Section.objects.get(slug="hero")

        with pytest.raises(ValidationError, match="different page"):
            Section.objects.create(page=other, parent=hero, name="Hero", slug="hero")


class TestSiteSettings:
    def test_there_is_only_ever_one_row(self, site: SiteSettings) -> None:
        SiteSettings(name={"en-us": "Second"}).save()

        assert SiteSettings.objects.count() == 1
        assert SiteSettings.load().name == {"en-us": "Second"}

    def test_reading_before_anybody_saved_returns_an_empty_one(self) -> None:
        assert SiteSettings.objects.exists() is False
        assert SiteSettings.load().name == {}

    def test_keywords_must_be_lists(self) -> None:
        with pytest.raises(ValidationError, match="list of words"):
            SiteSettings(name={"en-us": "Acme"}, keywords={"en-us": "acme"}).save()

    @override_settings(CMS_LANGUAGES=["en-us"])
    def test_a_language_that_was_removed_is_reported(self) -> None:
        with pytest.raises(ValidationError, match="Not a configured language: fa"):
            SiteSettings(name={"en-us": "Acme", "fa": "آکمی"}).save()
