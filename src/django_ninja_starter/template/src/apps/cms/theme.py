"""The admin theme this app uses when the project has one, and how it copes when not.

This app is meant to be copied into any Django project. This project themes its
admin with `Unfold <https://unfoldadmin.com>`_, and the content screen looks
native there because it uses Unfold's layout, widgets and filters. A project
that has never heard of Unfold should still be able to drop this app in and get
a working -- if plainer -- admin, rather than an ``ImportError`` at startup.

So every themed import is resolved here, once, with a Django equivalent behind
it. Nothing else in the app imports Unfold, and the difference between the two
worlds is this file plus one template variable.
"""

from typing import Any

from django import forms

try:  # pragma: no cover - exercised by whichever branch the project installs
    from unfold.admin import ModelAdmin, StackedInline, TabularInline
    from unfold.contrib.filters.admin import (
        BooleanRadioFilter,
        ChoicesDropdownFilter,
        RelatedDropdownFilter,
    )
    from unfold.decorators import display
    from unfold.widgets import (
        UnfoldAdminEmailInputWidget,
        UnfoldAdminSingleDateWidget,
        UnfoldAdminSplitDateTimeVerticalWidget,
        UnfoldAdminTextareaWidget,
        UnfoldAdminTextInputWidget,
        UnfoldAdminURLInputWidget,
        UnfoldBooleanSwitchWidget,
    )

    UNFOLD_INSTALLED = True
    ADMIN_BASE_TEMPLATE = "unfold/layouts/base_simple.html"

    TextInput = UnfoldAdminTextInputWidget
    Textarea = UnfoldAdminTextareaWidget
    EmailInput = UnfoldAdminEmailInputWidget
    URLInput = UnfoldAdminURLInputWidget
    DateInput = UnfoldAdminSingleDateWidget
    SplitDateTimeInput = UnfoldAdminSplitDateTimeVerticalWidget
    BooleanInput = UnfoldBooleanSwitchWidget
except ImportError:  # pragma: no cover - only in a project without Unfold
    from django.contrib.admin import ModelAdmin, StackedInline, TabularInline, display

    UNFOLD_INSTALLED = False
    ADMIN_BASE_TEMPLATE = "admin/base_site.html"

    # No themed filters without the theme; `dropdown_filter` reads these as
    # "use a plain field name instead".
    BooleanRadioFilter = ChoicesDropdownFilter = RelatedDropdownFilter = None

    class TextInput(forms.TextInput):  # type: ignore[no-redef]
        pass

    class Textarea(forms.Textarea):  # type: ignore[no-redef]
        pass

    class EmailInput(forms.EmailInput):  # type: ignore[no-redef]
        pass

    class URLInput(forms.URLInput):  # type: ignore[no-redef]
        pass

    class DateInput(forms.DateInput):  # type: ignore[no-redef]
        def __init__(self, attrs: dict[str, Any] | None = None, **kwargs: Any) -> None:
            super().__init__(attrs={**(attrs or {}), "type": "date"}, **kwargs)

    class SplitDateTimeInput(forms.SplitDateTimeWidget):  # type: ignore[no-redef]
        pass

    class BooleanInput(forms.CheckboxInput):  # type: ignore[no-redef]
        pass


def dropdown_filter(field: str, kind: Any) -> Any:
    """``(field, filter)`` where the theme provides one, plain ``field`` where not."""
    return (field, kind) if kind is not None else field


__all__ = [
    "ADMIN_BASE_TEMPLATE",
    "UNFOLD_INSTALLED",
    "BooleanInput",
    "BooleanRadioFilter",
    "ChoicesDropdownFilter",
    "DateInput",
    "EmailInput",
    "ModelAdmin",
    "RelatedDropdownFilter",
    "SplitDateTimeInput",
    "StackedInline",
    "TabularInline",
    "Textarea",
    "TextInput",
    "URLInput",
    "display",
    "dropdown_filter",
]
