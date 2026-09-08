"""Every field type, normalised and refused.

One test class per family rather than one per member, because what is worth
asserting is the family's promise: a scalar type accepts what it names and
nothing else, an addressable type expands a bare string into the full object, and
a choice type is the only one whose validity depends on the field it sits on.

The point of all of it is the promise the API makes to a client: a value of a
given ``type`` always has the same shape, so nothing downstream has to handle
"string or object" for the same field on two different pages.
"""

from datetime import UTC, date, datetime

import pytest
from django.core.exceptions import ValidationError

from apps.cms.fields import (
    ADDRESSABLE_TYPES,
    CHOICE_TYPES,
    LONG_TYPES,
    MEDIA_TYPES,
    OPEN_TYPES,
    UPLOAD_EXTENSIONS,
    FieldType,
    is_empty,
    normaliser_for,
    normalize_options,
    normalize_value,
    option_values,
    value_url,
)


class TestEveryTypeIsAccountedFor:
    """The tables other modules read have to cover the enum, not most of it."""

    def test_every_type_can_be_normalised(self) -> None:
        for field_type in FieldType.values:
            # Raises for an unknown type; a choice field with no options is
            # still a known type, which is what this is asking.
            assert normaliser_for(field_type) is not None

    def test_the_upload_table_covers_exactly_the_media_types(self) -> None:
        assert set(UPLOAD_EXTENSIONS) == set(MEDIA_TYPES)

    def test_the_families_do_not_overlap(self) -> None:
        assert MEDIA_TYPES <= ADDRESSABLE_TYPES
        assert not (OPEN_TYPES & ADDRESSABLE_TYPES)
        assert not (LONG_TYPES & CHOICE_TYPES)


class TestScalars:
    def test_text_types_take_text_and_refuse_a_number(self) -> None:
        for field_type in (FieldType.TEXT, FieldType.TEXTAREA, FieldType.HTML, FieldType.MARKDOWN):
            assert normalize_value(field_type, "# Hello") == "# Hello"
            with pytest.raises(ValidationError, match="Expected text"):
                normalize_value(field_type, 7)

    def test_a_number_is_not_a_boolean(self) -> None:
        assert normalize_value(FieldType.NUMBER, 9.5) == 9.5
        # `True == 1` in Python, so without the explicit check a checkbox would
        # be a perfectly acceptable price.
        with pytest.raises(ValidationError, match="Expected a number"):
            normalize_value(FieldType.NUMBER, True)

    def test_a_boolean_is_not_the_word_true(self) -> None:
        assert normalize_value(FieldType.BOOLEAN, False) is False
        with pytest.raises(ValidationError, match="true or false"):
            normalize_value(FieldType.BOOLEAN, "true")

    def test_dates_arrive_as_objects_and_leave_as_strings(self) -> None:
        assert normalize_value(FieldType.DATE, date(2026, 9, 3)) == "2026-09-03"
        assert normalize_value(FieldType.DATE, "2026-09-03") == "2026-09-03"
        with pytest.raises(ValidationError, match="YYYY-MM-DD"):
            normalize_value(FieldType.DATE, "the third")

    def test_a_datetime_keeps_its_offset(self) -> None:
        moment = datetime(2026, 9, 3, 10, 30, tzinfo=UTC)

        assert normalize_value(FieldType.DATETIME, moment) == "2026-09-03T10:30:00+00:00"

    def test_an_email_is_trimmed_and_checked(self) -> None:
        assert normalize_value(FieldType.EMAIL, " hi@example.com ") == "hi@example.com"
        with pytest.raises(ValidationError, match="email address"):
            normalize_value(FieldType.EMAIL, "hi@")

    def test_a_phone_number_keeps_the_spacing_it_was_written_with(self) -> None:
        """Copy, not data. "+44 20 7946 0958" is how it is meant to be read out."""
        assert normalize_value(FieldType.PHONE, "+44 20 7946 0958") == "+44 20 7946 0958"
        assert normalize_value(FieldType.PHONE, "(020) 7946 0958") == "(020) 7946 0958"
        with pytest.raises(ValidationError, match="phone number"):
            normalize_value(FieldType.PHONE, "ring us")

    def test_a_colour_is_hex_and_lower_cased(self) -> None:
        assert normalize_value(FieldType.COLOR, "#AABBCC") == "#aabbcc"
        assert normalize_value(FieldType.COLOR, "#abc") == "#abc"
        with pytest.raises(ValidationError, match="#rrggbb"):
            normalize_value(FieldType.COLOR, "rebeccapurple")

    def test_a_page_reference_is_a_slug_not_an_address(self) -> None:
        assert normalize_value(FieldType.PAGE, " about-us ") == "about-us"
        with pytest.raises(ValidationError, match="page slug"):
            normalize_value(FieldType.PAGE, "https://example.com/about-us")

    def test_a_url_is_checked_rather_than_taken_on_trust(self) -> None:
        assert normalize_value(FieldType.URL, "https://example.com") == "https://example.com"
        with pytest.raises(ValidationError, match="Expected a URL"):
            normalize_value(FieldType.URL, "example dot com")

    def test_an_address_on_this_site_is_a_url_too(self) -> None:
        """It has to be: it is what this app's own upload button produces."""
        assert normalize_value(FieldType.IMAGE, "/media/cms/hero.png")["url"] == (
            "/media/cms/hero.png"
        )

    def test_a_protocol_relative_address_is_not_an_address_on_this_site(self) -> None:
        """`//evil.example.com/x` looks internal and points somewhere else."""
        with pytest.raises(ValidationError, match="Expected a URL"):
            normalize_value(FieldType.URL, "//evil.example.com/x")


class TestAddressable:
    @pytest.mark.parametrize("field_type", sorted(MEDIA_TYPES))
    def test_a_bare_url_becomes_the_full_object(self, field_type: FieldType) -> None:
        assert normalize_value(field_type, "https://cdn.example.com/a") == {
            "url": "https://cdn.example.com/a",
            "title": None,
            "alt": None,
            "meta": {},
        }

    def test_a_link_has_no_alt_text_and_says_so(self) -> None:
        with pytest.raises(ValidationError, match="Unknown keys: alt"):
            normalize_value(FieldType.LINK, {"url": "https://example.com", "alt": "nope"})

    def test_anything_unnamed_belongs_under_meta(self) -> None:
        value = normalize_value(
            FieldType.IMAGE,
            {"url": "https://cdn.example.com/a.png", "meta": {"width": 1200}},
        )

        assert value["meta"] == {"width": 1200}

    def test_a_key_nobody_recognises_is_refused_rather_than_dropped(self) -> None:
        with pytest.raises(ValidationError, match="Put anything else under meta"):
            normalize_value(FieldType.IMAGE, {"url": "https://a.example.com/x", "width": 12})


class TestChoices:
    def test_bare_words_are_expanded_into_value_and_label(self) -> None:
        assert normalize_options(["small", "large"]) == [
            {"value": "small", "label": "small"},
            {"value": "large", "label": "large"},
        ]

    def test_a_value_and_a_label_may_differ(self) -> None:
        options = normalize_options([{"value": "sm", "label": "Small"}])

        assert options == [{"value": "sm", "label": "Small"}]
        assert option_values(options) == ["sm"]

    def test_a_duplicate_is_refused_by_position(self) -> None:
        with pytest.raises(ValidationError, match="Choice 2: small is listed twice"):
            normalize_options(["small", "small"])

    def test_an_empty_value_is_refused(self) -> None:
        with pytest.raises(ValidationError, match="non-empty word"):
            normalize_options([" "])

    def test_only_a_listed_choice_is_accepted(self) -> None:
        options = ["small", "large"]

        assert normalize_value(FieldType.SELECT, "small", options=options) == "small"
        with pytest.raises(ValidationError, match="Expected one of: small, large"):
            normalize_value(FieldType.SELECT, "huge", options=options)

    def test_a_choice_field_with_no_options_says_what_to_do(self) -> None:
        with pytest.raises(ValidationError, match="Add them to the field first"):
            normalize_value(FieldType.SELECT, "small", options=[])


class TestLists:
    def test_a_list_field_says_which_item_failed(self) -> None:
        with pytest.raises(ValidationError, match="Item 2: Expected a URL"):
            normalize_value(
                FieldType.IMAGE,
                ["https://cdn.example.com/a.png", "not a url"],
                multiple=True,
            )

    def test_a_list_field_refuses_a_single_value(self) -> None:
        with pytest.raises(ValidationError, match="Expected a list"):
            normalize_value(FieldType.TEXT, "one", multiple=True)

    def test_every_item_of_a_list_is_normalised(self) -> None:
        value = normalize_value(
            FieldType.IMAGE,
            ["https://cdn.example.com/a.png", {"url": "https://cdn.example.com/b.png"}],
            multiple=True,
        )

        assert [item["url"] for item in value] == [
            "https://cdn.example.com/a.png",
            "https://cdn.example.com/b.png",
        ]


class TestEmptiness:
    def test_false_and_zero_are_content(self) -> None:
        """The bug this prevents: an unticked switch reading as an unwritten field."""
        assert is_empty(False) is False
        assert is_empty(0) is False

    @pytest.mark.parametrize("value", [None, "", [], {}])
    def test_nothing_is_nothing(self, value: object) -> None:
        assert is_empty(value) is True


class TestValueUrl:
    def test_it_reads_an_address_out_of_either_shape(self) -> None:
        assert value_url("https://a.example.com/x") == "https://a.example.com/x"
        assert value_url({"url": "https://a.example.com/y"}) == "https://a.example.com/y"

    def test_anything_else_has_no_address(self) -> None:
        assert value_url(9) == ""
        assert value_url({"title": "no url here"}) == ""
