"""This app's own sidebar group and dashboard numbers.

See :mod:`infrastructure.common.adminui` for the protocol. The numbers chosen
are the two an operator acts on -- what has been sent lately, and how much of it
nobody has read -- rather than the size of the table, which changes every day
and means nothing on its own.
"""

from datetime import timedelta
from typing import Any

from django.db.models import Count, Q
from django.http import HttpRequest
from django.utils import timezone

from infrastructure.common.adminui import Section, card, changelist, item, may

NAVIGATION_ORDER = 20
DASHBOARD_ORDER = 20


def navigation(request: HttpRequest) -> dict[str, Any]:
    return {
        "title": "Notifications",
        "separator": False,
        "collapsible": False,
        "items": [
            item(
                "Notifications",
                "notifications",
                changelist("notifications", "notification"),
                "notifications.view_notification",
            ),
            item(
                "Read receipts",
                "mark_email_read",
                changelist("notifications", "notificationreceipt"),
                "notifications.view_notificationreceipt",
            ),
        ],
    }


def dashboard(request: HttpRequest) -> Section | None:
    if not may(request, "notifications.view_notification"):
        return None
    from apps.notifications.models import Audience, Notification

    week_ago = timezone.now() - timedelta(days=7)
    recent = Notification.objects.filter(created_at__gte=week_ago)
    totals = recent.aggregate(
        everybody=Count("id", filter=Q(audience=Audience.GLOBAL)),
        addressed=Count("id", filter=~Q(audience=Audience.GLOBAL)),
    )
    sent = (totals["everybody"] or 0) + (totals["addressed"] or 0)
    # Unread is counted over what was addressed to somebody: a global
    # announcement has no single reader to have missed it, and counting one
    # would make the number grow with the size of the audience. Read state is a
    # receipt rather than a column -- see the model -- so "unread" is the
    # addressed rows with no receipt that carries a `read_at`.
    unread = (
        recent.exclude(audience=Audience.GLOBAL).exclude(receipts__read_at__isnull=False).count()
    )
    return Section(
        title="Notifications",
        cards=[
            card(
                "Sent, last 7 days",
                sent,
                hint=f"{totals['everybody'] or 0} to everybody",
                icon="campaign",
                link=changelist("notifications", "notification"),
            ),
            card(
                "Still unread",
                unread,
                hint="of what was addressed to somebody"
                if totals["addressed"]
                else "nothing was addressed to anybody",
                icon="mark_email_unread",
                link=changelist("notifications", "notification"),
            ),
        ],
    )
