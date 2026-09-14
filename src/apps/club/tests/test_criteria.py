"""The condition matcher, and the refusals that stop a mission meaning nothing.

The important half is `validate`. A condition object with a typo in it that was
merely *ignored* would be a mission quietly paying XP for every event of its
kind, which is the opposite of what the operator wrote.
"""

import pytest

from apps.club.criteria import matches, validate
from apps.club.errors import MissionRefused


def test_empty_criteria_match_everything() -> None:
    assert matches({}, value=1, metadata={}) is True


def test_a_typo_is_refused_rather_than_ignored() -> None:
    with pytest.raises(MissionRefused, match="not a condition"):
        validate({"min_vlaue": 100})


def test_criteria_have_to_be_an_object() -> None:
    with pytest.raises(MissionRefused):
        validate([{"min_value": 1}])


def test_a_range_that_cannot_match_is_refused() -> None:
    with pytest.raises(MissionRefused, match="nothing could ever match"):
        validate({"min_value": 100, "max_value": 10})


def test_an_empty_in_list_is_refused() -> None:
    with pytest.raises(MissionRefused, match="non-empty list"):
        validate({"in": {"method": []}})


@pytest.mark.parametrize(
    ("criteria", "value", "metadata", "expected"),
    [
        ({"min_value": 100}, 100, {}, True),
        ({"min_value": 100}, 99, {}, False),
        ({"max_value": 100}, 101, {}, False),
        ({"equals": {"currency": "USD"}}, 1, {"currency": "USD"}, True),
        ({"equals": {"currency": "USD"}}, 1, {"currency": "EUR"}, False),
        ({"equals": {"currency": "USD"}}, 1, {}, False),
        ({"in": {"method": ["card", "cash"]}}, 1, {"method": "cash"}, True),
        ({"in": {"method": ["card", "cash"]}}, 1, {"method": "bank"}, False),
        ({"exists": ["coupon"]}, 1, {"coupon": "X"}, True),
        ({"exists": ["coupon"]}, 1, {}, False),
        ({"min_value": 10, "equals": {"kind": "a"}}, 20, {"kind": "a"}, True),
        ({"min_value": 10, "equals": {"kind": "a"}}, 20, {"kind": "b"}, False),
    ],
)
def test_matching(criteria: dict, value: float, metadata: dict, expected: bool) -> None:
    assert matches(criteria, value=value, metadata=metadata) is expected


def test_several_conditions_are_an_and() -> None:
    """Two conditions written means both, which is what an operator expects."""
    criteria = validate({"min_value": 10, "in": {"kind": ["gold"]}})

    assert matches(criteria, value=50, metadata={"kind": "gold"}) is True
    assert matches(criteria, value=5, metadata={"kind": "gold"}) is False
