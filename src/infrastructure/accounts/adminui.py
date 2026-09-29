"""People: the accounts group in the sidebar, and who has joined lately.

Always installed -- every other table in this project points at
``AUTH_USER_MODEL`` -- so this is the one contribution that is never absent.
Django's own ``auth.Group`` is listed here too: it belongs beside the accounts it
applies to rather than under a heading of its own.
"""

from datetime import timedelta
from typing import Any

from django.contrib.auth import get_user_model
from django.http import HttpRequest
from django.utils import timezone

from infrastructure.common.adminui import Section, card, changelist, item, may

NAVIGATION_ORDER = 50
DASHBOARD_ORDER = 50


def navigation(request: HttpRequest) -> dict[str, Any]:
    return {
        "title": "People",
        "order": 50,
        "separator": True,
        "collapsible": False,
        "items": [
            item("Accounts", "person", changelist("accounts", "user"), "accounts.view_user"),
            item("Profiles", "badge", changelist("accounts", "profile"), "accounts.view_profile"),
            item("Groups", "groups", changelist("auth", "group"), "auth.view_group"),
        ],
    }


def dashboard(request: HttpRequest) -> Section | None:
    if not may(request, "accounts.view_user"):
        return None
    users = get_user_model().objects
    week_ago = timezone.now() - timedelta(days=7)
    active = users.filter(is_active=True).count()
    total = users.count()
    return Section(
        title="People",
        cards=[
            card(
                "Accounts",
                total,
                hint=f"{users.filter(date_joined__gte=week_ago).count()} joined this week",
                icon="person",
                link=changelist("accounts", "user"),
            ),
            card(
                "Able to sign in",
                active,
                hint=f"{total - active} deactivated"
                if total - active
                else f"{users.filter(is_staff=True).count()} of them staff",
                icon="how_to_reg",
                link=changelist("accounts", "user"),
            ),
        ],
    )
