"""Validate the settings contracts declared by whichever apps are installed.

One check for all of them, so a new app gets validated by declaring a spec and
nothing else. Messages are keyed by the setting itself rather than by a serial
number, which means ``SILENCED_SYSTEM_CHECKS`` can turn off one nag without
turning off a whole family.
"""

from typing import Any

from django.apps import apps
from django.core.checks import CheckMessage, Error, Warning, register

from infrastructure.common.appsettings import MISSING, AppSettings, Requirement


def installed_specs() -> list[tuple[str, AppSettings]]:
    """Return ``(app label, spec)`` for every installed app that declares one."""
    found = []
    for config in apps.get_app_configs():
        spec = getattr(config, "settings_spec", None)
        if isinstance(spec, AppSettings):
            found.append((config.label, spec))
    return found


def _hint(requirement: Requirement) -> str:
    parts = []
    if requirement.env:
        parts.append(f"Set {requirement.env}.")
    if requirement.hint:
        parts.append(requirement.hint)
    return " ".join(parts)


def _numeric(value: Any) -> float | None:
    return float(value) if isinstance(value, int | float) and not isinstance(value, bool) else None


def _check(label: str, spec: AppSettings, requirement: Requirement) -> list[CheckMessage]:
    messages: list[CheckMessage] = []
    identifier = f"{label}.{requirement.check_id}"
    value = requirement.resolve()

    if value is MISSING:
        return [
            Error(
                f"{spec.title}: {requirement.setting} is not defined in settings",
                hint=_hint(requirement),
                id=identifier,
            )
        ]

    empty = value in ("", None, [], {})
    if empty and requirement.required:
        return [
            Error(
                f"{spec.title} needs {requirement.setting}: {requirement.purpose}",
                hint=_hint(requirement),
                id=identifier,
            )
        ]
    if empty and requirement.recommended:
        return [
            Warning(
                f"{spec.title} has no {requirement.setting}: {requirement.purpose}",
                hint=_hint(requirement),
                id=identifier,
            )
        ]
    if empty:
        return messages

    if requirement.choices and str(value) not in requirement.choices:
        messages.append(
            Error(
                f"{spec.title}: {requirement.setting} must be one of "
                f"{', '.join(requirement.choices)}",
                hint=_hint(requirement),
                id=identifier,
            )
        )
    number = _numeric(value)
    if number is not None:
        below = requirement.minimum is not None and number < requirement.minimum
        above = requirement.maximum is not None and number > requirement.maximum
        if below or above:
            messages.append(
                Error(
                    f"{spec.title}: {requirement.setting} must be between "
                    f"{requirement.minimum} and {requirement.maximum}",
                    hint=_hint(requirement),
                    id=identifier,
                )
            )
    if str(value) in requirement.unsafe_defaults:
        messages.append(
            Warning(
                f"{spec.title}: {requirement.setting} is still at a development default",
                hint=_hint(requirement),
                id=identifier,
            )
        )
    return messages


@register()
def check_declared_app_settings(**kwargs: object) -> list[CheckMessage]:
    """Run every installed app's settings contract."""
    messages: list[CheckMessage] = []
    for label, spec in installed_specs():
        for requirement in spec.requirements:
            messages.extend(_check(label, spec, requirement))
    return messages
