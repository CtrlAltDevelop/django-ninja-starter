"""This app's own sidebar group and dashboard numbers.

The club earns a card for the reason the wallet does: its failure mode is silent.
A club with no ladder, or with every mission switched off, does not error -- it
simply never pays anybody anything, and the first person to notice is a member
asking why they are still on level one. Both of those are a number on the front
page here.

See :mod:`infrastructure.common.adminui` for the protocol.
"""

from typing import Any

from django.db.models import Count
from django.http import HttpRequest

from infrastructure.common.adminui import Section, card, changelist, item, may

NAVIGATION_ORDER = 55
DASHBOARD_ORDER = 55


def navigation(request: HttpRequest) -> dict[str, Any]:
    """Members first: the club and its ladder are configured once, members arrive daily."""
    return {
        "title": "Club",
        "separator": False,
        "collapsible": False,
        "items": [
            item(
                "Members",
                "groups",
                changelist("club", "membership"),
                "club.view_membership",
            ),
            item("Clubs", "shield", changelist("club", "club"), "club.view_club"),
            item("Missions", "flag", changelist("club", "mission"), "club.view_mission"),
            item("XP awarded", "military_tech", changelist("club", "xpaward"), "club.view_xpaward"),
            item(
                "Mission progress",
                "trending_up",
                changelist("club", "missionprogress"),
                "club.view_missionprogress",
            ),
        ],
    }


def club_numbers() -> dict[str, int]:
    """The three questions somebody opens this page to ask."""
    from apps.club.models import Club, ClubStatus, Membership, MembershipStatus, Mission

    clubs = Club.objects.exclude(status=str(ClubStatus.ARCHIVED)).annotate(rungs=Count("levels"))
    return {
        "members": Membership.objects.filter(status=str(MembershipStatus.ACTIVE)).count(),
        "live_missions": Mission.objects.filter(is_enabled=True).count(),
        "ladderless": clubs.filter(rungs=0).count(),
        "clubs": clubs.count(),
    }


def dashboard(request: HttpRequest) -> Section | None:
    """Members, what they can earn, and whether anything is actually earnable."""
    if not may(request, "club.view_membership"):
        return None

    numbers = club_numbers()
    return Section(
        title="Club",
        cards=[
            card(
                "Members",
                numbers["members"],
                hint=f"across {numbers['clubs']} club{'s' if numbers['clubs'] != 1 else ''}",
                icon="groups",
                link=changelist("club", "membership"),
            ),
            card(
                # Bad at zero rather than good: a club with no live mission pays
                # nobody anything and reports no error while doing it.
                "Missions running",
                numbers["live_missions"],
                hint="nothing is earnable right now"
                if not numbers["live_missions"]
                else "earning XP",
                icon="flag",
                link=changelist("club", "mission"),
                tone="good" if numbers["live_missions"] else "warn",
            ),
            card(
                "Clubs with no ladder",
                numbers["ladderless"],
                hint="every club has rungs"
                if not numbers["ladderless"]
                else "members there have no level to reach",
                icon="stairs",
                link=changelist("club", "club"),
                tone="warn" if numbers["ladderless"] else "good",
            ),
        ],
    )
