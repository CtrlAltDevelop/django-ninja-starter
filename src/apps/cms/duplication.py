"""Copying a page, with everything on it.

The second page of a kind that already exists is the commonest page anybody
makes -- another product, another campaign, another country's version of the
same thing. Rebuilding its sections and fields by hand is tedious, and it is
also how two pages that were meant to match quietly stop matching.

What is copied is the structure *and* the content, because a copy with empty
boxes is only half the saving: an editor wants last month's page in front of
them to edit down. What is not copied is the publishing state -- the copy is
always a draft, since it arrives holding text nobody has reviewed.
"""

from django.db import transaction

from apps.cms.models import Field, Page, PageStatus, Section, SectionPlacement


def _free_slug(slug: str) -> str:
    """``about`` -> ``about-copy``, then ``about-copy-2``, and so on."""
    candidate = f"{slug}-copy"
    suffix = 2
    while Page.objects.filter(slug=candidate).exists():
        candidate = f"{slug}-copy-{suffix}"
        suffix += 1
    return candidate


def _copy_fields(source: Section, target: Section) -> None:
    Field.objects.bulk_create(
        [
            Field(
                section=target,
                name=field.name,
                slug=field.slug,
                help_text=field.help_text,
                field_type=field.field_type,
                multiple=field.multiple,
                required=field.required,
                order=field.order,
                is_active=field.is_active,
                values=field.values,
            )
            for field in source.fields.all()
        ]
    )


@transaction.atomic
def duplicate_page(page: Page) -> Page:
    """Return a draft copy of ``page``, sections, fields and placements included.

    ``bulk_create`` is deliberate here and only here: every row being copied was
    validated when it was first written, and re-validating a hundred of them one
    at a time turns a copy into a visible pause.
    """
    copy = Page(
        name=f"{page.name} (copy)",
        slug=_free_slug(page.slug),
        order=page.order,
        status=PageStatus.DRAFT,
        published_at=None,
        title=page.title,
        description=page.description,
        keywords=page.keywords,
        og_image=page.og_image,
    )
    copy.save()

    for section in page.sections.filter(parent__isnull=True):
        new_section = Section(
            page=copy,
            name=section.name,
            slug=section.slug,
            order=section.order,
            is_active=section.is_active,
        )
        new_section.save()
        _copy_fields(section, new_section)
        for child in section.children.all():
            new_child = Section(
                page=copy,
                parent=new_section,
                name=child.name,
                slug=child.slug,
                order=child.order,
                is_active=child.is_active,
            )
            new_child.save()
            _copy_fields(child, new_child)

    # Shared sections are placed, not copied -- that is the whole point of them.
    SectionPlacement.objects.bulk_create(
        [
            SectionPlacement(
                page=copy,
                section=placement.section,
                order=placement.order,
                is_active=placement.is_active,
            )
            for placement in page.placements.all()
        ]
    )
    return copy
