"""The credential rows this token mode owns, under the shared Credentials heading."""

from typing import Any

from django.http import HttpRequest

from infrastructure.common.adminui import changelist, item

NAVIGATION_ORDER = 63


def navigation(request: HttpRequest) -> dict[str, Any]:
    return {
        "title": "Credentials",
        "order": 60,
        "separator": True,
        "collapsible": True,
        "items": [
            item(
                "Sliding tokens",
                "timer",
                changelist("oauth_sliding", "slidingtoken"),
                "oauth_sliding.view_slidingtoken",
            )
        ],
    }
