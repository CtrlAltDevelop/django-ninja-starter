"""The credential rows this token mode owns, under the shared Credentials heading."""

from typing import Any

from django.http import HttpRequest

from infrastructure.common.adminui import changelist, item

NAVIGATION_ORDER = 62


def navigation(request: HttpRequest) -> dict[str, Any]:
    return {
        "title": "Credentials",
        "order": 60,
        "separator": True,
        "collapsible": True,
        "items": [
            item(
                "Sessions",
                "devices",
                changelist("oauth_session", "oauthsession"),
                "oauth_session.view_oauthsession",
            )
        ],
    }
