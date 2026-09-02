"""Write every piece of content to one JSON document.

Content is data a person typed, which makes it the part of a deployment least
likely to exist anywhere else. A database dump is not an answer -- it carries
ids, hashes and half the auth tables with it, and it cannot be reviewed in a
pull request or copied from staging into a local database.

So the export is the content and nothing else: slugs instead of ids, every
language of every value, and the same shape the importer reads back. It is what
seeds a new environment, what moves a rewritten page from staging to production,
and what makes "who changed this?" answerable with a diff.
"""

import json
from pathlib import Path
from typing import Any

from django.core.management.base import BaseCommand, CommandParser

from apps.cms.models import Field, Menu, Page, Section, SiteSettings

FORMAT_VERSION = 1


def _field(field: Field) -> dict[str, Any]:
    return {
        "slug": field.slug,
        "name": field.name,
        "help_text": field.help_text,
        "field_type": field.field_type,
        "multiple": field.multiple,
        "required": field.required,
        "order": field.order,
        "is_active": field.is_active,
        "values": field.values,
    }


def _section(section: Section) -> dict[str, Any]:
    return {
        "slug": section.slug,
        "name": section.name,
        "order": section.order,
        "is_active": section.is_active,
        "fields": [_field(field) for field in section.fields.all()],
        "children": [
            {
                "slug": child.slug,
                "name": child.name,
                "order": child.order,
                "is_active": child.is_active,
                "fields": [_field(field) for field in child.fields.all()],
            }
            for child in section.children.all()
        ],
    }


def _page(page: Page) -> dict[str, Any]:
    return {
        "slug": page.slug,
        "name": page.name,
        "order": page.order,
        "status": page.status,
        "published_at": page.published_at.isoformat() if page.published_at else None,
        "title": page.title,
        "description": page.description,
        "keywords": page.keywords,
        "og_image": page.og_image,
        "sections": [
            _section(section) for section in page.sections.all() if section.parent_id is None
        ],
        "shared": [
            {
                "section": placement.section.slug,
                "order": placement.order,
                "is_active": placement.is_active,
            }
            for placement in page.placements.all()
        ],
    }


def _menu(menu: Menu) -> dict[str, Any]:
    def item(entry: Any) -> dict[str, Any]:
        return {
            "label": entry.label,
            "page": entry.page.slug if entry.page_id else None,
            "url": entry.url,
            "new_tab": entry.new_tab,
            "order": entry.order,
            "is_active": entry.is_active,
            "children": [item(child) for child in entry.children.all()],
        }

    return {
        "slug": menu.slug,
        "name": menu.name,
        "is_active": menu.is_active,
        "items": [item(entry) for entry in menu.items.all() if entry.parent_id is None],
    }


def export_content() -> dict[str, Any]:
    """The whole content tree, keyed by slug rather than by id."""
    site = SiteSettings.load()
    pages = Page.objects.prefetch_related(
        "sections__fields", "sections__children__fields", "placements__section"
    )
    library = Section.objects.filter(page__isnull=True).prefetch_related(
        "fields", "children__fields"
    )
    menus = Menu.objects.prefetch_related("items__children", "items__page")
    return {
        "version": FORMAT_VERSION,
        "site": {
            "name": site.name,
            "tagline": site.tagline,
            "description": site.description,
            "keywords": site.keywords,
            "logo": site.logo,
            "favicon": site.favicon,
            "og_image": site.og_image,
            "contact": site.contact,
            "social_links": site.social_links,
            "extra": site.extra,
        },
        "library": [_section(section) for section in library],
        "pages": [_page(page) for page in pages],
        "menus": [_menu(menu) for menu in menus],
    }


class Command(BaseCommand):
    help = "Write all CMS content to a JSON document"

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument(
            "--output",
            help="file to write (defaults to standard output, so it can be piped)",
        )
        parser.add_argument(
            "--indent", type=int, default=2, help="JSON indentation (0 for one line)"
        )

    def handle(self, *args: Any, **options: Any) -> None:
        document = json.dumps(
            export_content(),
            indent=options["indent"] or None,
            ensure_ascii=False,
            sort_keys=False,
        )
        if not options["output"]:
            self.stdout.write(document)
            return
        path = Path(options["output"])
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"{document}\n", encoding="utf-8")
        self.stdout.write(self.style.SUCCESS(f"Wrote {path}"))
