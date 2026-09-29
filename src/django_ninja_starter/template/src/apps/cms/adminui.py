"""What this app puts in the admin's sidebar and on its front page.

The app owns this rather than the project, for the reason the whole app owns
itself: a deployment that has not enabled the CMS should get an admin with no
Content group and no content cards, and that should cost nobody a line in a
project file. Installing the app is what puts them there.

See :mod:`infrastructure.common.adminui` for the protocol both functions answer.
"""

from typing import Any

from django.http import HttpRequest
from django.urls import reverse_lazy

from infrastructure.common.adminui import Panel, Section, card, changelist, item, may

#: Content leads the sidebar and the dashboard: on a site that has one, it is
#: what most people open the admin to change.
NAVIGATION_ORDER = 10
DASHBOARD_ORDER = 10


def navigation(request: HttpRequest) -> dict[str, Any]:
    return {
        "title": "Content",
        "separator": False,
        "collapsible": False,
        "items": [
            item("Pages", "web", changelist("cms", "page"), "cms.view_page", "cms.change_field"),
            item("Sections", "view_agenda", changelist("cms", "section"), "cms.view_section"),
            item(
                "Shared sections",
                "content_copy",
                changelist("cms", "sectionplacement"),
                "cms.view_sectionplacement",
            ),
            item("Fields", "text_fields", changelist("cms", "field"), "cms.view_field"),
            item("Menus", "menu", changelist("cms", "menu"), "cms.view_menu"),
            item(
                "Site events",
                "event",
                changelist("cms", "siteevent"),
                "cms.view_siteevent",
                "cms.change_siteevent",
            ),
            item(
                "Site settings",
                "public",
                changelist("cms", "sitesettings"),
                "cms.view_sitesettings",
                "cms.change_sitesettings",
            ),
        ],
    }


def dashboard(request: HttpRequest) -> Section | None:
    """Counts, plus the two things an editor actually acts on.

    What is *missing* rather than what exists: a required field nobody has
    written is a page that renders wrong, and a date whose reminder window has
    opened is something to do this week. Both are absent when the reader may not
    see them, and the section disappears when everything in it is.
    """
    section = Section(title="Content", cards=[], panels=[])
    if may(request, "cms.view_page", "cms.change_field"):
        section["cards"] = _cards()
        section["panels"] = [_coverage()]
    if may(request, "cms.view_siteevent"):
        events = _events()
        if events:
            section["panels"] = [*section.get("panels", []), events]
    return section


def _numbers() -> dict[str, Any]:
    """Counted in one place, because the cards and the coverage bars share it."""
    from apps.cms.models import Field, Page
    from apps.cms.models import Section as ContentSection
    from apps.cms.translations import default_language, known_languages

    fields = Field.objects.filter(is_active=True)
    total = fields.count()
    # A field is translated into a language when its values object has a key for
    # it, so completeness is countable without loading a single value.
    languages = known_languages()
    written = {
        language: fields.filter(**{f"values__{language}__isnull": False}).count()
        for language in languages
    }
    missing = sum(
        1
        for field in fields.filter(required=True).only("required", "values")
        if default_language() not in field.values
    )
    return {
        # Live rather than "all": the number an editor recognises is the number
        # of pages a reader can actually reach.
        "pages": Page.objects.live().count(),
        "drafts": Page.objects.exclude(status="published").count(),
        "sections": ContentSection.objects.filter(is_active=True).count(),
        "fields": total,
        "languages": languages,
        "written": written,
        "required_missing": missing,
    }


def _cards() -> list[Any]:
    numbers = _numbers()
    drafts = numbers["drafts"]
    missing = numbers["required_missing"]
    return [
        card(
            "Published pages",
            numbers["pages"],
            hint=(f"{numbers['sections']} sections" + (f" · {drafts} draft(s)" if drafts else "")),
            icon="web",
            link=changelist("cms", "page"),
        ),
        card(
            "Content fields",
            numbers["fields"],
            hint=f"across {len(numbers['languages'])} language(s)",
            icon="text_fields",
            link=changelist("cms", "field"),
        ),
        card(
            "Required, still empty",
            missing,
            hint="fields a page still needs" if missing else "everything required is written",
            icon="warning",
            link=changelist("cms", "field"),
            tone="bad" if missing else "good",
        ),
    ]


def _coverage() -> Panel:
    numbers = _numbers()
    total = numbers["fields"]
    return Panel(
        title="Translation coverage",
        bars=[
            {
                "label": language,
                "value": f"{round(count / total * 100) if total else 0}%",
                "percent": round(count / total * 100) if total else 0,
            }
            for language, count in numbers["written"].items()
        ],
        note=(
            "A field with no translation falls back to the default language, "
            "so a page always renders."
        ),
    )


def _events() -> Panel | None:
    """The dates whose reminder window is open, soonest first.

    A list rather than a number, because "three things are coming up" is not
    actionable and "the certificate expires on Tuesday" is.
    """
    from apps.cms.models import due_events

    events = list(due_events())
    if not events:
        return None
    return Panel(
        title="Upcoming site events",
        rows=[
            {
                "label": event.name,
                "hint": f"{event.get_kind_display()} · {event.next_date():%-d %b %Y}",
                "right": (
                    "overdue"
                    if event.is_overdue()
                    else "today"
                    if event.days_away() == 0
                    else f"in {event.days_away()} day(s)"
                ),
                "tone": "bad" if event.is_overdue() else "",
                "link": reverse_lazy("admin:cms_siteevent_change", args=(event.pk,)),
            }
            # Five, because a dashboard block that scrolls is a block nobody
            # reads to the bottom of, and the list itself is one click away.
            for event in events[:5]
        ],
        link=changelist("cms", "siteevent"),
        link_label=f"{len(events)} in all" if len(events) > 5 else "",
    )
