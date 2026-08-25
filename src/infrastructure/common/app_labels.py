"""Ask whether an optional app is installed, by label rather than import path.

Every optional app in this project is addressed by its Django label -- that is
what ``apps.get_model`` takes, and what a migration names. Testing for one with
``apps.is_installed`` would mean also knowing where its package happens to live,
so moving a package would silently turn a live feature check into a permanent
``False``.
"""

from django.apps import apps


def app_installed(app_label: str) -> bool:
    """Return whether an app with this label is in ``INSTALLED_APPS``."""
    try:
        apps.get_app_config(app_label)
    except LookupError:
        return False
    return True
