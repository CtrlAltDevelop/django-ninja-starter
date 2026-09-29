"""Deciding whether one occurrence satisfies one mission's conditions.

A tiny matcher over a plain object, and deliberately tiny. The temptation with
"which events count?" is an expression language, and an expression language
stored in a database column and evaluated at runtime is a remote code execution
waiting for somebody with admin access to have a bad day. So there is no `eval`
here and no callables in the JSON: five operators, all of them comparisons.

    {}                                        every event of that key counts
    {"min_value": 100}                        value >= 100
    {"max_value": 500}                        value <= 500
    {"equals": {"currency": "USD"}}           metadata["currency"] == "USD"
    {"in": {"method": ["card", "cash"]}}      metadata["method"] in [...]
    {"exists": ["coupon"]}                    metadata has a "coupon" key

Several together are an AND, because an operator writing two conditions means
both. Anything else in the object is refused when the mission is saved rather
than ignored at match time -- a typo'd condition that silently matches
everything is a mission paying XP for the wrong thing.
"""

from typing import Any

from apps.club.errors import MissionRefused

#: The only keys a criteria object may carry.
OPERATORS = ("min_value", "max_value", "equals", "in", "exists")


def validate(criteria: Any) -> dict[str, Any]:
    """Refuse a condition object that cannot mean anything, at the point it is written."""
    if criteria in (None, {}):
        return {}
    if not isinstance(criteria, dict):
        raise MissionRefused("Criteria have to be an object.")
    unknown = sorted(set(criteria) - set(OPERATORS))
    if unknown:
        raise MissionRefused(
            f"{', '.join(repr(key) for key in unknown)} is not a condition this app "
            f"understands. Use one of: {', '.join(OPERATORS)}."
        )
    for bound in ("min_value", "max_value"):
        if bound in criteria and not isinstance(criteria[bound], int | float):
            raise MissionRefused(f"{bound!r} has to be a number.")
    low, high = criteria.get("min_value"), criteria.get("max_value")
    if low is not None and high is not None and low > high:
        raise MissionRefused("min_value is above max_value, so nothing could ever match.")
    for mapping in ("equals", "in"):
        if mapping in criteria and not isinstance(criteria[mapping], dict):
            raise MissionRefused(f"{mapping!r} has to be an object of field to value.")
    if "in" in criteria:
        for field, allowed in criteria["in"].items():
            if not isinstance(allowed, list | tuple) or not allowed:
                raise MissionRefused(f"'in' for {field!r} has to be a non-empty list.")
    if "exists" in criteria and not isinstance(criteria["exists"], list | tuple):
        raise MissionRefused("'exists' has to be a list of field names.")
    return dict(criteria)


def matches(criteria: dict[str, Any], *, value: float, metadata: dict[str, Any]) -> bool:
    """Whether this occurrence counts. Empty criteria match everything."""
    if not criteria:
        return True
    low = criteria.get("min_value")
    if low is not None and value < low:
        return False
    high = criteria.get("max_value")
    if high is not None and value > high:
        return False
    for field, expected in criteria.get("equals", {}).items():
        if metadata.get(field) != expected:
            return False
    for field, allowed in criteria.get("in", {}).items():
        if metadata.get(field) not in allowed:
            return False
    return all(field in metadata for field in criteria.get("exists", []))
