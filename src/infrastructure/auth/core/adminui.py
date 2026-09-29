"""Sign-ins: the phone numbers people are known by, and how logging in has gone.

Two headings, both shared. "People" is the accounts app's, and this adds one
item to it; "Audit" is collapsed and is read after something went wrong, which
is why a deployment with no authentication apps has no such heading at all --
see :func:`infrastructure.common.adminui.sidebar_navigation` on merging.
"""

from datetime import timedelta
from typing import Any

from django.db.models import Count, Q
from django.http import HttpRequest
from django.utils import timezone

from infrastructure.common.adminui import Panel, Section, changelist, item, may

NAVIGATION_ORDER = 55
DASHBOARD_ORDER = 60


def navigation(request: HttpRequest) -> list[dict[str, Any]]:
    return [
        {
            "title": "People",
            "order": 50,
            "separator": True,
            "collapsible": False,
            "items": [
                item(
                    "Phone numbers",
                    "smartphone",
                    changelist("auth_core", "phonenumber"),
                    "auth_core.view_phonenumber",
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
                    "Sign-in events",
                    "history",
                    changelist("auth_core", "authevent"),
                    "auth_core.view_authevent",
                )
            ],
        },
    ]


def dashboard(request: HttpRequest) -> Section | None:
    """How sign-ins have gone this week, and by which method.

    A rate rather than two counts on their own: ninety failures matter when
    there were a hundred attempts and matter much less when there were nine
    thousand, and the number somebody reacts to is the proportion.
    """
    if not may(request, "auth_core.view_authevent"):
        return None
    from infrastructure.auth.core.models import AuthEvent

    week_ago = timezone.now() - timedelta(days=7)
    recent = AuthEvent.objects.filter(created_at__gte=week_ago)
    totals = recent.aggregate(
        succeeded=Count("id", filter=Q(event_type="login_succeeded")),
        failed=Count("id", filter=Q(event_type="login_failed")),
    )
    succeeded = totals["succeeded"] or 0
    failed = totals["failed"] or 0
    attempts = succeeded + failed
    by_method = (
        recent.filter(event_type="login_succeeded")
        .values("method")
        .annotate(count=Count("id"))
        .order_by("-count")[:5]
    )
    most = max((row["count"] for row in by_method), default=0)
    return Section(
        title="Sign-ins",
        panels=[
            Panel(
                title="Sign-ins, last 7 days",
                figures=[
                    {"label": "Succeeded", "value": succeeded},
                    {"label": "Failed", "value": failed, "tone": "bad" if failed else ""},
                    {
                        "label": "Success rate",
                        "value": f"{round(succeeded / attempts * 100) if attempts else 0}%",
                    },
                ],
                bars=[
                    {
                        "label": row["method"] or "unknown",
                        "value": row["count"],
                        # Against the busiest method rather than against the
                        # total: the bars are there to compare methods with each
                        # other, and four short stubs compare nothing.
                        "percent": round(row["count"] / most * 100) if most else 0,
                    }
                    for row in by_method
                ],
                note="" if by_method else "Nobody has signed in this week.",
                link=changelist("auth_core", "authevent"),
                wide=True,
            )
        ],
    )
