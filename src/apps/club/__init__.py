"""The club app's public surface: one function, and the registry behind it.

    from apps.club import track
    track(user, "shop.order.paid", value=99.0, reference=f"order:{order.pk}")

Imported lazily so that importing this package does not pull in models before
Django's app registry is ready -- a plain `from apps.club.services import track`
at module scope in another app's `apps.py` would.
"""

from typing import Any

__all__ = ["track"]


def track(
    user: Any,
    key: str,
    *,
    value: float = 1.0,
    metadata: dict[str, Any] | None = None,
    reference: str = "",
) -> list[dict[str, Any]]:
    """Tell the club app something happened. See :func:`apps.club.services.track`.

    Returns nothing when the app is not installed, which is what lets another
    app call this unconditionally: importing the services would import models
    Django has not registered, and fail.
    """
    from django.apps import apps

    if not apps.is_installed("apps.club"):
        return []
    from apps.club.services import track as _track

    return _track(user, key, value=value, metadata=metadata, reference=reference)
