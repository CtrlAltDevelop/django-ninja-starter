"""What OAuth core puts in the sidebar: its clients, and the trail it leaves.

Two headings, both shared with whichever other credential apps this deployment
installed -- see :func:`infrastructure.common.adminui.sidebar_navigation`.
"""

from typing import Any

from django.http import HttpRequest

from infrastructure.common.adminui import changelist, item

NAVIGATION_ORDER = 65


def navigation(request: HttpRequest) -> list[dict[str, Any]]:
    return [
        {
            "title": "Credentials",
            "order": 60,
            "separator": True,
            "collapsible": True,
            "items": [
                item(
                    "OAuth clients",
                    "apps",
                    changelist("oauth_core", "oauthclient"),
                    "oauth_core.view_oauthclient",
                )
            ],
        },
        {
            "title": "Audit",
            "order": 70,
            "separator": True,
            "collapsible": True,
            "items": [
                item(
                    "OAuth events",
                    "fact_check",
                    changelist("oauth_core", "oauthauditevent"),
                    "oauth_core.view_oauthauditevent",
                ),
                item(
                    "Social login attempts",
                    "swap_horiz",
                    changelist("oauth_core", "socialloginattempt"),
                    "oauth_core.view_socialloginattempt",
                ),
            ],
        },
    ]
