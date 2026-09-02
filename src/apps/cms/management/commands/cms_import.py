"""Read a content document back, by slug.

The import is deliberately *not* a restore. It matches on slugs and updates what
it finds, so running it twice does nothing the second time, and running a
staging export against production edits the pages that exist rather than
replacing the database. Every row goes through the models, so a document with a
value of the wrong shape is refused with the same message an editor would see
rather than written and discovered later.

``--prune`` is the exception, and it says so: it deletes the pages, library
sections and menus the document does not mention. That is what "make this
environment look exactly like that one" needs, and it is not what anybody wants
by accident.
"""

import json
from pathlib import Path
from typing import Any

from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError, CommandParser
from django.db import transaction
from django.utils.dateparse import parse_datetime

from apps.cms.models import (
    Field,
    Menu,
    MenuItem,
    Page,
    Section,
    SectionPlacement,
    SiteSettings,
)


def _fields(section: Section, rows: list[dict[str, Any]]) -> None:
    for row in rows:
        # Fetched-or-built rather than get_or_create: every write here is
        # validated, and a row created empty and filled in afterwards would be
        # refused halfway through for being empty.
        field = Field.objects.filter(section=section, slug=row["slug"]).first() or Field(
            section=section, slug=row["slug"]
        )
        field.name = row.get("name", row["slug"])
        field.help_text = row.get("help_text", "")
        field.field_type = row.get("field_type", field.field_type)
        field.multiple = row.get("multiple", False)
        field.required = row.get("required", False)
        field.order = row.get("order", 0)
        field.is_active = row.get("is_active", True)
        field.values = row.get("values", {})
        field.save()


def _section(page: Page | None, row: dict[str, Any], parent: Section | None = None) -> Section:
    section = Section.objects.filter(page=page, slug=row["slug"]).first() or Section(
        page=page, slug=row["slug"]
    )
    section.name = row.get("name", section.name or row["slug"])
    section.order = row.get("order", 0)
    section.is_active = row.get("is_active", True)
    section.parent = parent
    section.save()
    _fields(section, row.get("fields", []))
    for child in row.get("children", []):
        _section(page, child, parent=section)
    return section


def _page(row: dict[str, Any]) -> Page:
    page = Page.objects.filter(slug=row["slug"]).first() or Page(slug=row["slug"])
    page.name = row.get("name", page.name or row["slug"])
    page.order = row.get("order", 0)
    page.status = row.get("status", page.status)
    published_at = row.get("published_at")
    page.published_at = parse_datetime(published_at) if published_at else None
    page.title = row.get("title", {})
    page.description = row.get("description", {})
    page.keywords = row.get("keywords", {})
    page.og_image = row.get("og_image", "")
    page.save()
    for section_row in row.get("sections", []):
        _section(page, section_row)
    return page


def _menu(row: dict[str, Any]) -> Menu:
    menu = Menu.objects.filter(slug=row["slug"]).first() or Menu(slug=row["slug"])
    menu.name = row.get("name", menu.name or row["slug"])
    menu.is_active = row.get("is_active", True)
    menu.save()
    # Entries have no slug of their own, so they are replaced wholesale. They
    # are a short ordered list; matching them by label would break the moment
    # somebody fixed a typo.
    menu.items.all().delete()

    def entry(item_row: dict[str, Any], parent: MenuItem | None = None) -> None:
        page_slug = item_row.get("page")
        item = MenuItem(
            menu=menu,
            parent=parent,
            label=item_row.get("label", {}),
            page=Page.objects.filter(slug=page_slug).first() if page_slug else None,
            url=item_row.get("url", ""),
            new_tab=item_row.get("new_tab", False),
            order=item_row.get("order", 0),
            is_active=item_row.get("is_active", True),
        )
        item.save()
        for child in item_row.get("children", []):
            entry(child, parent=item)

    for item_row in row.get("items", []):
        entry(item_row)
    return menu


class Command(BaseCommand):
    help = "Read CMS content from a JSON document written by cms_export"

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument("path", help="the JSON document to read")
        parser.add_argument(
            "--prune",
            action="store_true",
            help="delete pages, library sections and menus the document does not mention",
        )

    def handle(self, *args: Any, **options: Any) -> None:
        path = Path(options["path"])
        try:
            document = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise CommandError(f"Cannot read {path}: {error}") from error
        if not isinstance(document, dict):
            raise CommandError("A content document is a JSON object.")

        try:
            with transaction.atomic():
                counts = self._apply(document, prune=options["prune"])
        except ValidationError as error:
            # One bad value must not leave half a page imported.
            raise CommandError(f"Refused: {'; '.join(error.messages)}") from error

        for label, count in counts.items():
            self.stdout.write(f"{label}: {count}")
        self.stdout.write(self.style.SUCCESS(f"Imported {path}"))

    def _apply(self, document: dict[str, Any], *, prune: bool) -> dict[str, int]:
        site_row = document.get("site")
        if site_row:
            site = SiteSettings.load()
            for key, value in site_row.items():
                setattr(site, key, value)
            site.save()

        library = [_section(None, row) for row in document.get("library", [])]
        pages = [_page(row) for row in document.get("pages", [])]

        placements = 0
        for row in document.get("pages", []):
            page = Page.objects.get(slug=row["slug"])
            page.placements.all().delete()
            for shared in row.get("shared", []):
                section = Section.objects.filter(slug=shared["section"], page__isnull=True).first()
                if section is None:
                    raise CommandError(
                        f"Page {row['slug']} places a library section that is not in this "
                        f"document: {shared['section']}"
                    )
                SectionPlacement.objects.create(
                    page=page,
                    section=section,
                    order=shared.get("order", 0),
                    is_active=shared.get("is_active", True),
                )
                placements += 1

        menus = [_menu(row) for row in document.get("menus", [])]

        removed = 0
        if prune:
            removed += Page.objects.exclude(slug__in=[page.slug for page in pages]).delete()[0]
            removed += (
                Section.objects.filter(page__isnull=True)
                .exclude(slug__in=[section.slug for section in library])
                .delete()[0]
            )
            removed += Menu.objects.exclude(slug__in=[menu.slug for menu in menus]).delete()[0]

        return {
            "pages": len(pages),
            "library sections": len(library),
            "shared placements": placements,
            "menus": len(menus),
            "deleted rows": removed,
        }
