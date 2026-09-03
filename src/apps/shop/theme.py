"""The admin theme this app uses when the project has one, and how it copes when not.

This app is meant to be copied into any Django project. This project themes its
admin with `Unfold <https://unfoldadmin.com>`_, and the product screen looks
native there because it uses Unfold's layout, filters and decorators. A project
that has never heard of Unfold should still be able to drop this app in and get
a working -- if plainer -- admin, rather than an ``ImportError`` at startup.

So every themed import is resolved here, once, with a Django equivalent behind
it. Nothing else in the app imports Unfold.
"""

from typing import Any

try:  # pragma: no cover - exercised by whichever branch the project installs
    from unfold.admin import ModelAdmin, StackedInline, TabularInline
    from unfold.contrib.filters.admin import (
        BooleanRadioFilter,
        ChoicesDropdownFilter,
        RangeNumericFilter,
        RelatedDropdownFilter,
    )
    from unfold.decorators import display

    UNFOLD_INSTALLED = True
except ImportError:  # pragma: no cover - only in a project without Unfold
    from django.contrib.admin import ModelAdmin, StackedInline, TabularInline, display

    UNFOLD_INSTALLED = False

    # No themed filters without the theme; `dropdown_filter` reads these as
    # "use a plain field name instead".
    BooleanRadioFilter = ChoicesDropdownFilter = None
    RangeNumericFilter = RelatedDropdownFilter = None


def dropdown_filter(field: str, kind: Any) -> Any:
    """``(field, filter)`` where the theme provides one, plain ``field`` where not."""
    return (field, kind) if kind is not None else field


__all__ = [
    "UNFOLD_INSTALLED",
    "BooleanRadioFilter",
    "ChoicesDropdownFilter",
    "ModelAdmin",
    "RangeNumericFilter",
    "RelatedDropdownFilter",
    "StackedInline",
    "TabularInline",
    "display",
    "dropdown_filter",
]
