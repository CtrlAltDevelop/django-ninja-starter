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
        UnfoldAdminColorInputWidget,
        UnfoldAdminDecimalFieldWidget,
        UnfoldAdminEmailInputWidget,
        UnfoldAdminFileFieldWidget,
        UnfoldAdminImageFieldWidget,
        UnfoldAdminSelectMultipleWidget,
        UnfoldAdminSelectWidget,
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
    NumberInput = UnfoldAdminDecimalFieldWidget
    ColorInput = UnfoldAdminColorInputWidget
    Select = UnfoldAdminSelectWidget
    SelectMultiple = UnfoldAdminSelectMultipleWidget
    FileInput = UnfoldAdminFileFieldWidget
    ImageInput = UnfoldAdminImageFieldWidget

    class TelInput(UnfoldAdminTextInputWidget):
        """A phone box. Unfold has no ``tel`` widget, so it is a text one told to be one.

        ``type="tel"`` is what makes a phone keypad open on a handset, which is
        the only device where typing a number into a text box is unpleasant.
        """

        input_type = "tel"
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

    class NumberInput(forms.NumberInput):  # type: ignore[no-redef]
        pass

    class ColorInput(forms.TextInput):  # type: ignore[no-redef]
        def __init__(self, attrs: dict[str, Any] | None = None, **kwargs: Any) -> None:
            super().__init__(attrs={**(attrs or {}), "type": "color"}, **kwargs)

    class TelInput(forms.TextInput):  # type: ignore[no-redef]
        input_type = "tel"

    class Select(forms.Select):  # type: ignore[no-redef]
        pass

    class SelectMultiple(forms.SelectMultiple):  # type: ignore[no-redef]
        pass

    class FileInput(forms.ClearableFileInput):  # type: ignore[no-redef]
        pass

    class ImageInput(forms.ClearableFileInput):  # type: ignore[no-redef]
        pass


def dropdown_filter(field: str, kind: Any) -> Any:
    """``(field, filter)`` where the theme provides one, plain ``field`` where not."""
    return (field, kind) if kind is not None else field


class MultiFileInput(forms.ClearableFileInput):
    """One input that takes several files at once.

    Django's own file widget is deliberately single: ``value_from_datadict``
    returns one file even when the browser sent five, because a ``FileField``
    can only hold one. A *list* field here holds many, so the widget has to hand
    all of them over and the form field below has to be ready for a list.
    """

    allow_multiple_selected = True

    def __init__(self, attrs: dict[str, Any] | None = None) -> None:
        super().__init__(attrs={**(attrs or {}), "multiple": True})

    def value_from_datadict(self, data: Any, files: Any, name: str) -> Any:
        if hasattr(files, "getlist"):
            return files.getlist(name)
        single = files.get(name)
        return [single] if single else []


__all__ = [
    "ADMIN_BASE_TEMPLATE",
    "UNFOLD_INSTALLED",
    "BooleanInput",
    "BooleanRadioFilter",
    "ChoicesDropdownFilter",
    "ColorInput",
    "DateInput",
    "EmailInput",
    "FileInput",
    "ImageInput",
    "ModelAdmin",
    "MultiFileInput",
    "NumberInput",
    "RelatedDropdownFilter",
    "Select",
    "SelectMultiple",
    "SplitDateTimeInput",
    "StackedInline",
    "TabularInline",
    "TelInput",
    "Textarea",
    "TextInput",
    "URLInput",
    "display",
    "dropdown_filter",
]
