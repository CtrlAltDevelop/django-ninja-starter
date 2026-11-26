"""This app's sidebar group and dashboard numbers. See :mod:`infrastructure.common.adminui`."""

from typing import Any

from django.db.models import Sum
from django.http import HttpRequest

from infrastructure.common.adminui import Section, card, changelist, item, may

NAVIGATION_ORDER = 45
DASHBOARD_ORDER = 45


def navigation(request: HttpRequest) -> dict[str, Any]:
    return {
        "title": "Files",
        "separator": False,
        "collapsible": False,
        "items": [
            item("Files", "folder", changelist("files", "storedfile"), "files.view_storedfile"),
        ],
    }


def dashboard(request: HttpRequest) -> Section | None:
    if not may(request, "files.view_storedfile"):
        return None
    from apps.files.models import StoredFile

    totals = StoredFile.objects.aggregate(size=Sum("size"))
    return Section(
        title="Files",
        cards=[
            card(
                "Stored files",
                StoredFile.objects.count(),
                hint=f"{(totals['size'] or 0) // (1024 * 1024)} MB in storage",
                icon="folder",
                link=changelist("files", "storedfile"),
            ),
        ],
    )
