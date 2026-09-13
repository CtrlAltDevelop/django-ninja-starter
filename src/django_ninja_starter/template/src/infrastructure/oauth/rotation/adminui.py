"""The credential rows this token mode owns, under the shared Credentials heading."""

from typing import Any

from django.http import HttpRequest

from infrastructure.common.adminui import changelist, item

NAVIGATION_ORDER = 61


def navigation(request: HttpRequest) -> dict[str, Any]:
    return {
        "title": "Credentials",
        "order": 60,
        "separator": True,
        "collapsible": True,
        "items": [
            item(
                "Token families",
                "key",
                changelist("oauth_rotation", "tokenfamily"),
                "oauth_rotation.view_tokenfamily",
            )
        ],
    }
