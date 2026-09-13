"""Second factors, under the People heading the accounts app opens."""

from typing import Any

from django.http import HttpRequest

from infrastructure.common.adminui import changelist, item

NAVIGATION_ORDER = 56


def navigation(request: HttpRequest) -> dict[str, Any]:
    return {
        "title": "People",
        "order": 50,
        "separator": True,
        "collapsible": False,
        "items": [
            item(
                "Second factors",
                "encrypted",
                changelist("auth_twofactor", "secondfactor"),
                "auth_twofactor.view_secondfactor",
            )
        ],
    }
