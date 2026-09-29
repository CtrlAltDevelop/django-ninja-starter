"""This app's own sidebar group and dashboard numbers.

The desk gets a section on the front page because it is the one app here whose
work is somebody *waiting*: a ticket nobody has answered is worse every minute,
which is exactly what a dashboard is for and exactly what a list of models never
says. ``admin.py`` has carried :func:`~apps.support.admin.desk_numbers` since
this app was written; until this module existed, nothing called it.

See :mod:`infrastructure.common.adminui` for the protocol.
"""

from typing import Any

from django.http import HttpRequest

from infrastructure.common.adminui import Section, admin_url, card, changelist, item, may

NAVIGATION_ORDER = 30
DASHBOARD_ORDER = 30


def navigation(request: HttpRequest) -> dict[str, Any]:
    """Live chat leads: it is the only item here somebody opens because a person
    is waiting. Everything under it is a record read afterwards or a setting
    changed once."""
    items = []
    chat = admin_url("admin:support_live_chat")
    if chat:
        items.append(item("Live chat", "forum", chat, "support.view_ticket"))
    items.extend(
        [
            item(
                "Tickets",
                "confirmation_number",
                changelist("support", "ticket"),
                "support.view_ticket",
            ),
            item(
                "Categories", "category", changelist("support", "category"), "support.view_category"
            ),
            item("Tags", "label", changelist("support", "tag"), "support.view_tag"),
            item(
                "Canned replies",
                "quickreply",
                changelist("support", "cannedreply"),
                "support.view_cannedreply",
            ),
            item(
                "Attachments",
                "attach_file",
                changelist("support", "attachment"),
                "support.view_attachment",
            ),
        ]
    )
    return {"title": "Support", "separator": False, "collapsible": False, "items": items}


def dashboard(request: HttpRequest) -> Section | None:
    """Three numbers, and the first one is a door.

    "Waiting for a first answer" leads because it is the promise the desk made
    and the only one a client experiences as silence. It links to the live
    screen rather than to the ticket list, because the answer to a number that
    says somebody is waiting is to go and answer them.
    """
    if not may(request, "support.view_ticket"):
        return None
    from apps.support.admin import desk_numbers

    numbers = desk_numbers()
    queue = changelist("support", "ticket")
    return Section(
        title="Support",
        cards=[
            card(
                "Waiting for an answer",
                numbers["awaiting"],
                hint="nobody has replied to these yet"
                if numbers["awaiting"]
                else "every open thread has been answered",
                icon="forum",
                link=admin_url("admin:support_live_chat") or queue,
                tone="warn" if numbers["awaiting"] else "good",
            ),
            card(
                "Open conversations",
                numbers["live"],
                hint=f"{numbers['unassigned']} with nobody on them"
                if numbers["unassigned"]
                else "all of them have an owner",
                icon="support_agent",
                link=queue,
            ),
            card(
                "Past their promise",
                numbers["breached"],
                hint="the SLA has been missed" if numbers["breached"] else "inside the SLA",
                icon="schedule",
                link=queue,
                tone="bad" if numbers["breached"] else "good",
            ),
        ],
    )
