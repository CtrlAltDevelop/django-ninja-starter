"""The eight attribute types, and what each of them accepts.

These are pure functions with no database behind them, which is the point: the
same normaliser runs for an admin form, a JSON import and a data migration, so
what it accepts is worth stating once and in one place.
"""

from datetime import date, datetime
from typing import Any

import pytest
from django.core.exceptions import ValidationError

from apps.shop.attributes import (
    AttributeType,
    clean_choices,
    is_empty,
    normalize_value,
)


def normalize(kind: Any, value: object, *choices: str) -> object:
    """``kind`` is `Any` for the reason `normalize_value` takes it that way: a
    `TextChoices` member reads as its `(value, label)` tuple to a checker."""
    return normalize_value(kind, value, choices=choices)


class TestText:
    def test_it_trims(self) -> None:
        assert normalize(AttributeType.TEXT, "  Aluminium  ") == "Aluminium"

    def test_nothing_but_spaces_is_not_an_answer(self) -> None:
        with pytest.raises(ValidationError):
            normalize(AttributeType.TEXT, "   ")

    def test_a_number_is_not_text(self) -> None:
        with pytest.raises(ValidationError):
            normalize(AttributeType.TEXT, 12)


class TestNumber:
    def test_a_string_from_a_form_is_accepted(self) -> None:
        assert normalize(AttributeType.NUMBER, " 16 ") == 16

    def test_a_whole_number_stays_whole(self) -> None:
        """So that 16 does not reach a spec table as 16.0."""
        assert normalize(AttributeType.NUMBER, 16.0) == 16
        assert isinstance(normalize(AttributeType.NUMBER, 16.0), int)

    def test_a_fraction_is_kept(self) -> None:
        assert normalize(AttributeType.NUMBER, "13.3") == 13.3

    def test_a_boolean_is_not_a_number(self) -> None:
        with pytest.raises(ValidationError):
            normalize(AttributeType.NUMBER, True)

    def test_words_are_not_a_number(self) -> None:
        with pytest.raises(ValidationError):
            normalize(AttributeType.NUMBER, "fourteen")

    def test_a_list_is_not_a_number(self) -> None:
        with pytest.raises(ValidationError):
            normalize(AttributeType.NUMBER, [14])


class TestBoolean:
    @pytest.mark.parametrize(
        ("given", "expected"),
        [(True, True), (False, False), ("yes", True), ("No", False), ("TRUE", True)],
    )
    def test_what_a_form_and_what_json_both_send(self, given: object, expected: bool) -> None:
        assert normalize(AttributeType.BOOLEAN, given) is expected

    def test_anything_else_is_refused(self) -> None:
        with pytest.raises(ValidationError):
            normalize(AttributeType.BOOLEAN, "maybe")


class TestChoice:
    def test_one_of_the_list_is_accepted(self) -> None:
        assert normalize(AttributeType.CHOICE, "M", "S", "M", "L") == "M"

    def test_anything_else_says_what_is_allowed(self) -> None:
        with pytest.raises(ValidationError) as refusal:
            normalize(AttributeType.CHOICE, "XL", "S", "M", "L")

        assert "S, M, L" in str(refusal.value)


class TestMultiChoice:
    def test_a_single_value_becomes_a_list(self) -> None:
        assert normalize(AttributeType.MULTI_CHOICE, "HDMI", "HDMI", "USB-C") == ["HDMI"]

    def test_the_declared_order_is_the_stored_order(self) -> None:
        """So two products with the same three options compare equal."""
        assert normalize(AttributeType.MULTI_CHOICE, ["USB-C", "HDMI"], "HDMI", "USB-C") == [
            "HDMI",
            "USB-C",
        ]

    def test_the_same_value_twice_is_stored_once(self) -> None:
        assert normalize(AttributeType.MULTI_CHOICE, ["HDMI", "HDMI"], "HDMI", "USB-C") == ["HDMI"]

    def test_something_not_on_the_list_is_refused(self) -> None:
        with pytest.raises(ValidationError):
            normalize(AttributeType.MULTI_CHOICE, ["VGA"], "HDMI", "USB-C")

    def test_an_object_is_not_a_list_of_values(self) -> None:
        with pytest.raises(ValidationError):
            normalize(AttributeType.MULTI_CHOICE, {"port": "HDMI"}, "HDMI")


class TestColour:
    def test_a_hex_colour_is_upper_cased(self) -> None:
        """So that #fff and #FFF are one value rather than two."""
        assert normalize(AttributeType.COLOR, "#1a2b3c") == "#1A2B3C"

    def test_a_short_form_is_accepted(self) -> None:
        assert normalize(AttributeType.COLOR, "#abc") == "#ABC"

    def test_a_name_is_not_a_colour(self) -> None:
        with pytest.raises(ValidationError):
            normalize(AttributeType.COLOR, "red")

    def test_something_the_length_of_a_colour_but_not_hex_is_refused(self) -> None:
        with pytest.raises(ValidationError):
            normalize(AttributeType.COLOR, "#zzz")


class TestDate:
    def test_an_iso_string_is_kept(self) -> None:
        assert normalize(AttributeType.DATE, "2026-03-01") == "2026-03-01"

    def test_a_date_object_is_accepted(self) -> None:
        assert normalize(AttributeType.DATE, date(2026, 3, 1)) == "2026-03-01"

    def test_a_datetime_keeps_only_the_day(self) -> None:
        assert normalize(AttributeType.DATE, datetime(2026, 3, 1, 14, 30)) == "2026-03-01"

    def test_another_format_is_refused(self) -> None:
        with pytest.raises(ValidationError):
            normalize(AttributeType.DATE, "01/03/2026")


class TestUrl:
    def test_a_url_is_accepted(self) -> None:
        assert normalize(AttributeType.URL, "https://example.test/manual.pdf") == (
            "https://example.test/manual.pdf"
        )

    def test_a_bare_word_is_not_a_url(self) -> None:
        with pytest.raises(ValidationError):
            normalize(AttributeType.URL, "manual.pdf")


class TestUnknownType:
    def test_it_says_which_type_it_did_not_recognise(self) -> None:
        with pytest.raises(ValidationError) as refusal:
            normalize_value("hologram", "anything")

        assert "hologram" in str(refusal.value)


class TestIsEmpty:
    @pytest.mark.parametrize("value", [None, "", [], {}])
    def test_nothing_filled_in(self, value: object) -> None:
        assert is_empty(value)

    @pytest.mark.parametrize("value", [False, 0, 0.0, "no", ["a"]])
    def test_a_falsey_answer_is_still_an_answer(self, value: object) -> None:
        """`False` and `0` are what somebody typed, not an empty box."""
        assert not is_empty(value)


class TestCleanChoices:
    def test_nothing_declared_is_an_empty_list(self) -> None:
        assert clean_choices(None) == []
        assert clean_choices("") == []

    def test_a_comma_separated_string_is_split(self) -> None:
        assert clean_choices("S, M, L") == ["S", "M", "L"]

    def test_the_same_choice_twice_is_refused(self) -> None:
        """It would otherwise put the same option in a dropdown twice."""
        with pytest.raises(ValidationError):
            clean_choices(["S", "S"])

    def test_a_list_of_something_other_than_text_is_refused(self) -> None:
        with pytest.raises(ValidationError):
            clean_choices([1, 2])

    def test_blank_entries_are_dropped(self) -> None:
        assert clean_choices(["S", "  ", "M"]) == ["S", "M"]
