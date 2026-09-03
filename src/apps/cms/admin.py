"""Two kinds of admin for two kinds of person.

The **structure** -- which pages exist, what sections they are built from, which
fields are in each and of what type -- is a design decision with consequences
for whoever renders it. Adding a field means somebody has to display it. So
pages, sections and fields are superuser-only, and they are edited the ordinary
Django way: a page with its sections and shared placements inline, a section
with its fields inline.

The **content** is everybody else's. It gets its own screen, reached from the
page list, and it is nothing like a Django change form: one page at a time, one
language at a time, section by section in the order a reader meets them, with
the widget each field's type deserves. Nobody filling in a page should have to
learn what a JSON object is, or which of forty rows in a field list belongs to
the section they are looking at. A library section -- one shared by many pages
-- has the same screen of its own, and is badged wherever it appears so that
nobody edits nine pages thinking they are editing one.

That screen is permission-driven rather than superuser-only -- ``cms.change_field``
is what it asks for -- so a content editor can be given exactly it and nothing
else.

The theme is whatever :mod:`apps.cms.theme` resolves: Unfold where the project
installs it, Django's own admin where it does not.
"""

from typing import Any
from uuid import UUID

from django.contrib import admin, messages
from django.core.exceptions import PermissionDenied
from django.db.models import Count, Prefetch, QuerySet
from django.http import HttpRequest, HttpResponse, HttpResponseRedirect
from django.shortcuts import get_object_or_404
from django.template.response import TemplateResponse
from django.urls import URLPattern, path, reverse
from django.utils import timezone
from django.utils.html import format_html

from apps.cms.duplication import duplicate_page
from apps.cms.fields import MEDIA_TYPES, FieldType, value_url
from apps.cms.forms import (
    ContentForm,
    SiteSettingsForm,
    field_key,
    site_translation_fields,
)
from apps.cms.models import (
    Field,
    Menu,
    MenuItem,
    Page,
    Section,
    SectionPlacement,
    SiteSettings,
)
from apps.cms.preview import preview_url
from apps.cms.theme import (
    ADMIN_BASE_TEMPLATE,
    BooleanRadioFilter,
    ChoicesDropdownFilter,
    ModelAdmin,
    RelatedDropdownFilter,
    TabularInline,
    display,
    dropdown_filter,
)
from apps.cms.translations import default_language, known_languages, match_language

CONTENT_PERMISSION = "cms.change_field"


class CompletenessFilter(admin.SimpleListFilter):
    """ "Which required fields are still empty?" -- the list an editor works from.

    A column can show completeness one row at a time; only a filter can answer
    it for a site with four hundred fields, which is the size at which the
    question starts being asked.
    """

    title = "Filled in"
    parameter_name = "filled"

    def lookups(self, request: HttpRequest, model_admin: Any) -> list[tuple[str, str]]:
        return [("missing", "Required, still empty"), ("done", "Required and written")]

    def queryset(self, request: HttpRequest, queryset: QuerySet[Any]) -> QuerySet[Any]:
        if self.value() not in {"missing", "done"}:
            return queryset
        required = queryset.filter(required=True)
        written = required.filter(**{f"values__{default_language()}__isnull": False})
        return written if self.value() == "done" else required.exclude(pk__in=written)


class StructureAdmin(ModelAdmin):
    """Visible to any staff member, changeable only by a superuser.

    Read access matters: an editor who cannot see the structure cannot work out
    why the field they were told about is not on the content screen.
    """

    # Structure is edited rarely and carefully, and a half-finished section lost
    # to a stray back button is an afternoon gone.
    warn_unsaved_form = True
    list_filter_submit = True

    def has_add_permission(self, request: HttpRequest) -> bool:
        return bool(request.user.is_superuser)

    def has_change_permission(self, request: HttpRequest, obj: Any = None) -> bool:
        return bool(request.user.is_superuser)

    def has_delete_permission(self, request: HttpRequest, obj: Any = None) -> bool:
        return bool(request.user.is_superuser)


class SectionInline(TabularInline):
    """The page's own sections, in the order they appear on it."""

    model = Section
    extra = 0
    fields = ("name", "slug", "parent", "order", "is_active")
    ordering = ("order", "name")
    show_change_link = True

    def formfield_for_foreignkey(self, db_field: Any, request: HttpRequest, **kwargs: Any) -> Any:
        """Offer only this page's top-level sections as a parent.

        Without this the dropdown lists every section anywhere, and the model
        has to reject most of what it offers -- which is a validation error
        where a shorter list would have done.
        """
        if db_field.name == "parent":
            page_id = (
                request.resolver_match.kwargs.get("object_id") if request.resolver_match else None
            )
            queryset = Section.objects.filter(parent__isnull=True)
            kwargs["queryset"] = queryset.filter(page_id=page_id) if page_id else queryset.none()
        return super().formfield_for_foreignkey(db_field, request, **kwargs)


class PlacementInline(TabularInline):
    """Library sections placed on this page, sorted in with the page's own."""

    model = SectionPlacement
    extra = 0
    fields = ("section", "order", "is_active")
    ordering = ("order",)
    verbose_name = "shared section"
    verbose_name_plural = "shared sections"

    def formfield_for_foreignkey(self, db_field: Any, request: HttpRequest, **kwargs: Any) -> Any:
        if db_field.name == "section":
            kwargs["queryset"] = Section.objects.filter(page__isnull=True).order_by("name")
        return super().formfield_for_foreignkey(db_field, request, **kwargs)


class FieldInline(TabularInline):
    """The fields of one section: their structure, never their content.

    ``values`` is absent on purpose. Editing content here would mean typing JSON
    with a language key, next to the checkbox that decides the type it has to
    match -- which is the screen this app exists to replace.
    """

    model = Field
    extra = 0
    fields = (
        "name",
        "slug",
        "field_type",
        "multiple",
        "required",
        "order",
        "help_text",
        "is_active",
    )
    ordering = ("order", "name")


class ContentScreenMixin:
    """The shared half of the two content screens.

    A page and a library section are edited by the same form on the same
    template; they differ only in which sections are handed to it and what the
    heading says.
    """

    def content_context(
        self,
        request: HttpRequest,
        *,
        title: str,
        subtitle: str,
        content_url: str,
        sections: list[Section],
        form: ContentForm,
        language: str,
        extra: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return {
            **self.admin_site.each_context(request),  # type: ignore[attr-defined]
            "title": title,
            "subtitle": subtitle,
            "opts": self.opts,  # type: ignore[attr-defined]
            "form": form,
            "language": language,
            "languages": known_languages(),
            "default_language": default_language(),
            "content_url": content_url,
            "groups": _groups(sections, form),
            "base_template": ADMIN_BASE_TEMPLATE,
            **(extra or {}),
        }

    def save_content(self, request: HttpRequest, form: ContentForm, language: str) -> bool:
        """Save if valid, and say so to the person who pressed the button."""
        if not form.is_valid():
            self.message_user(  # type: ignore[attr-defined]
                request, "Fix the errors below and save again.", messages.ERROR
            )
            return False
        changed = form.save()
        self.message_user(  # type: ignore[attr-defined]
            request,
            f"Saved {changed} field(s) in {language}."
            if changed
            else f"Nothing changed in {language}.",
            messages.SUCCESS if changed else messages.INFO,
        )
        return True


def _previews(field: Field, language: str) -> list[dict[str, Any]]:
    """What this media field currently points at, ready to be shown back.

    An address in a box is not a picture. An editor replacing the hero image
    needs to see the one that is there -- otherwise checking which of nine
    uploads is live means opening nine URLs in new tabs.
    """
    if field.field_type not in MEDIA_TYPES:
        return []
    value = field.values.get(language)
    items = value if isinstance(value, list) else [value]
    return [
        {"url": url, "is_image": field.field_type == FieldType.IMAGE}
        for url in (value_url(item) for item in items)
        if url
    ]


def _rows(section: Section, form: ContentForm) -> list[dict[str, Any]]:
    return [
        {
            "field": field,
            "inputs": [name for name in form.fields if name.startswith(field_key(field))],
            "previews": _previews(field, form.language),
        }
        for field in section.fields.all()
    ]


def _groups(sections: list[Section], form: ContentForm) -> list[dict[str, Any]]:
    """Lay the bound inputs out the way the content itself is laid out."""

    def bound(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return [
            {
                "field": row["field"],
                "inputs": [form[name] for name in row["inputs"]],
                "previews": row["previews"],
            }
            for row in rows
        ]

    return [
        {
            "section": section,
            "shared": section.page_id is None,
            "rows": bound(_rows(section, form)),
            "children": [
                {"section": child, "rows": bound(_rows(child, form))}
                for child in section.children.all()
            ],
        }
        for section in sections
    ]


def _section_tree_prefetch() -> tuple[Prefetch, Prefetch]:
    """Sections and their children with every field, hidden ones included.

    The API hides what is switched off; the person who switched it off still has
    to be able to edit it, or turning a section back on would mean turning it on
    to see what is in it.
    """
    fields = Prefetch("fields", queryset=Field.objects.order_by("order", "name"))
    children = Prefetch(
        "children",
        queryset=Section.objects.order_by("order", "name").prefetch_related(fields),
    )
    return fields, children


@admin.register(Page)
class PageAdmin(ContentScreenMixin, StructureAdmin):
    """Pages, their publishing state, and the door to the content screen."""

    list_display = ("page_header", "state", "structure", "translations", "edit_content")
    list_filter = (
        dropdown_filter("status", ChoicesDropdownFilter),
        dropdown_filter("published_at", None),
    )
    search_fields = ("name", "slug")
    list_disable_select_all = True
    ordering = ("order", "name")
    inlines = (SectionInline, PlacementInline)
    readonly_fields = ("id", "created_at", "updated_at", "preview_link")
    actions = ("publish_now", "unpublish", "duplicate")
    fieldsets = (
        (None, {"fields": ("id", "name", "slug", "order")}),
        (
            "Publishing",
            {
                "fields": ("status", "published_at", "preview_link"),
                "description": (
                    "A draft is invisible to the API. Publishing with a future date "
                    "means the page goes live on its own."
                ),
            },
        ),
        (
            "Search",
            {
                "fields": ("title", "description"),
                "description": 'Per language, as {"en-us": "..."}. Blank falls back to the site.',
            },
        ),
        (
            "Sharing (Open Graph)",
            {
                "fields": ("og_title", "og_description", "og_image", "og_url"),
                "description": (
                    "What a link to this page looks like in a chat window or a "
                    "timeline. Anything left blank falls back to the meta values "
                    "above, then to the site's. Meta keywords are deliberately "
                    "absent: nothing has read them for over a decade."
                ),
                "classes": ("collapse",),
            },
        ),
        ("Dates", {"fields": ("created_at", "updated_at")}),
    )

    def get_queryset(self, request: HttpRequest) -> QuerySet[Page]:
        """The list counts sections and fields, so fetch both trees."""
        return (
            super()
            .get_queryset(request)
            .prefetch_related("sections__fields", "placements__section__fields")
        )

    @display(description="Page", header=True)
    def page_header(self, obj: Page) -> list[str]:
        """The two-line cell: what it is called, and what it is asked for."""
        return [obj.name, f"/{obj.slug}"]

    @display(
        description="State",
        label={"Published": "success", "Scheduled": "warning", "Draft": "info"},
        ordering="status",
    )
    def state(self, obj: Page) -> str:
        if obj.is_live:
            return "Published"
        return "Scheduled" if obj.is_scheduled else "Draft"

    @display(description="Structure")
    def structure(self, obj: Page) -> str:
        own = list(obj.sections.all())
        shared = [placement.section for placement in obj.placements.all()]
        fields = sum(len(section.fields.all()) for section in [*own, *shared])
        shared_note = f" (+{len(shared)} shared)" if shared else ""
        return f"{len(own)} sections{shared_note} · {fields} fields"

    @display(description="Written", label=True)
    def translations(self, obj: Page) -> str:
        """How much of this page exists in each language, at a glance.

        The number an editor is looking for is not "how many fields" but "how
        many are still blank in the language I am responsible for".
        """
        sections = [*obj.sections.all(), *(p.section for p in obj.placements.all())]
        written = []
        for language in known_languages():
            filled = sum(
                1
                for section in sections
                for field in section.fields.all()
                if language in field.values
            )
            written.append(f"{language} {filled}")
        return " · ".join(written)

    @display(description="Content")
    def edit_content(self, obj: Page) -> str:
        url = reverse("admin:cms_page_content", args=(obj.pk,))
        return format_html(
            '<a href="{}" class="bg-primary-600 font-medium px-3 py-1 rounded-default '
            'text-white text-sm whitespace-nowrap dark:bg-primary-500">Edit content</a>',
            url,
        )

    @display(description="Preview link")
    def preview_link(self, obj: Page) -> str:
        """A signed link that shows this page whatever its state.

        Readonly and shown on the page itself, because the person who needs it
        is the person looking at the draft -- and clickable, because the next
        thing they do with it is open it.
        """
        if obj.pk is None:
            return "Saved pages get a preview link."
        url = preview_url(obj)
        return format_html(
            '<a href="{}" target="_blank" rel="noopener" class="underline">{}</a>', url, url
        )

    @admin.action(description="Publish now")
    def publish_now(self, request: HttpRequest, queryset: QuerySet[Page]) -> None:
        published = 0
        for page in queryset:
            page.publish(at=timezone.now())
            page.save()
            published += 1
        self.message_user(request, f"Published {published} page(s).", messages.SUCCESS)

    @admin.action(description="Move back to draft")
    def unpublish(self, request: HttpRequest, queryset: QuerySet[Page]) -> None:
        drafted = 0
        for page in queryset:
            page.unpublish()
            page.save()
            drafted += 1
        self.message_user(request, f"Moved {drafted} page(s) back to draft.", messages.WARNING)

    @admin.action(description="Duplicate as a draft")
    def duplicate(self, request: HttpRequest, queryset: QuerySet[Page]) -> None:
        """Copy the structure, the content and the shared placements.

        The second page of a kind that already exists is the commonest page
        anybody makes, and rebuilding its sections and fields by hand is both
        tedious and the way two pages quietly stop matching. The copy is always
        a draft: it arrives with last month's copy in it and nobody meant to
        publish that.
        """
        copied = 0
        for page in queryset:
            copy = duplicate_page(page)
            copied += 1
            self.message_user(request, f"Copied {page.name} to {copy.slug}.", messages.SUCCESS)
        if not copied:
            self.message_user(request, "Nothing selected.", messages.INFO)

    def get_urls(self) -> list[URLPattern]:
        """Put the content screen ahead of the admin's catch-all object route."""
        content = path(
            "<uuid:page_id>/content/",
            self.admin_site.admin_view(self.content_view),
            name="cms_page_content",
        )
        return [content, *super().get_urls()]

    def _editable_page(self, page_id: UUID) -> Page:
        fields, children = _section_tree_prefetch()
        sections = Prefetch(
            "sections",
            queryset=Section.objects.filter(parent__isnull=True)
            .order_by("order", "name")
            .prefetch_related(fields, children),
        )
        placements = Prefetch(
            "placements",
            queryset=SectionPlacement.objects.order_by("order").prefetch_related(
                Prefetch(
                    "section",
                    queryset=Section.objects.prefetch_related(fields, children),
                )
            ),
        )
        return get_object_or_404(Page.objects.prefetch_related(sections, placements), pk=page_id)

    @staticmethod
    def _ordered_sections(page: Page) -> list[Section]:
        """The page's own sections and its shared ones, in the reader's order."""
        rows: list[tuple[int, str, Section]] = [
            (section.order, section.name, section) for section in page.sections.all()
        ]
        rows.extend(
            (placement.order, placement.section.name, placement.section)
            for placement in page.placements.all()
        )
        return [section for _, _, section in sorted(rows, key=lambda row: (row[0], row[1]))]

    def content_view(self, request: HttpRequest, page_id: UUID) -> HttpResponse:
        """Edit one page's content, in one language, section by section."""
        if not request.user.has_perm(CONTENT_PERMISSION):
            raise PermissionDenied
        page = self._editable_page(page_id)
        sections = self._ordered_sections(page)
        language = match_language(request.GET.get("language")) or default_language()
        url = reverse("admin:cms_page_content", args=(page.pk,))

        if request.method == "POST":
            # `request.FILES` as well as `request.POST`: a media field is
            # answered by an upload as readily as by a pasted address, and a
            # form built without the files would save the page and quietly drop
            # every picture on it.
            form = ContentForm(request.POST, request.FILES, sections=sections, language=language)
            if self.save_content(request, form, language):
                return HttpResponseRedirect(f"{url}?language={language}")
        else:
            form = ContentForm(sections=sections, language=language)

        return TemplateResponse(
            request,
            "admin/cms/content.html",
            self.content_context(
                request,
                title=f"{page.name} content ({language})",
                subtitle=f"/{page.slug}",
                content_url=url,
                sections=sections,
                form=form,
                language=language,
                extra={
                    "page": page,
                    "state": self.state(page),
                    "preview": preview_url(page),
                    "structure_url": reverse("admin:cms_page_change", args=(page.pk,)),
                    "back_url": reverse("admin:cms_page_changelist"),
                },
            ),
        )


@admin.register(Section)
class SectionAdmin(ContentScreenMixin, StructureAdmin):
    """A section and the fields it holds -- on a page, or in the library."""

    list_display = (
        "section_header",
        "belongs_to",
        "used_on",
        "field_count",
        "active",
        "edit_content",
    )
    list_filter = (
        dropdown_filter("page", RelatedDropdownFilter),
        dropdown_filter("is_active", BooleanRadioFilter),
    )
    search_fields = ("name", "slug", "page__name")
    ordering = ("page", "order", "name")
    autocomplete_fields = ("page",)
    inlines = (FieldInline,)
    readonly_fields = ("id", "created_at", "updated_at")
    list_select_related = ("page", "parent")

    def get_queryset(self, request: HttpRequest) -> QuerySet[Section]:
        return (
            super()
            .get_queryset(request)
            .prefetch_related("fields")
            .annotate(placement_count=Count("placements", distinct=True))
        )

    @display(description="Section", header=True)
    def section_header(self, obj: Section) -> list[str]:
        return [obj.name, obj.slug]

    @display(description="Belongs to", label={"Library": "warning"})
    def belongs_to(self, obj: Section) -> str:
        return obj.page.name if obj.page_id else "Library"

    @display(description="Used on")
    def used_on(self, obj: Section) -> str:
        """For a library section, the thing that makes editing it consequential."""
        if obj.page_id:
            return "—"
        count = getattr(obj, "placement_count", 0)
        return f"{count} page(s)"

    @display(description="Fields")
    def field_count(self, obj: Section) -> int:
        return len(obj.fields.all())

    @display(description="Shown", boolean=True)
    def active(self, obj: Section) -> bool:
        return obj.is_active

    @display(description="Content")
    def edit_content(self, obj: Section) -> str:
        """The same door pages have. A shared section has no page to reach it from."""
        url = reverse("admin:cms_section_content", args=(obj.pk,))
        return format_html(
            '<a href="{}" class="bg-primary-600 font-medium px-3 py-1 rounded-default '
            'text-white text-sm whitespace-nowrap dark:bg-primary-500">Edit content</a>',
            url,
        )

    def get_urls(self) -> list[URLPattern]:
        content = path(
            "<uuid:section_id>/content/",
            self.admin_site.admin_view(self.content_view),
            name="cms_section_content",
        )
        return [content, *super().get_urls()]

    def content_view(self, request: HttpRequest, section_id: UUID) -> HttpResponse:
        """The same screen as a page's, for one shared section."""
        if not request.user.has_perm(CONTENT_PERMISSION):
            raise PermissionDenied
        fields, children = _section_tree_prefetch()
        section = get_object_or_404(
            Section.objects.prefetch_related(fields, children), pk=section_id
        )
        language = match_language(request.GET.get("language")) or default_language()
        url = reverse("admin:cms_section_content", args=(section.pk,))
        used_on = SectionPlacement.objects.filter(section=section).count()

        if request.method == "POST":
            form = ContentForm(request.POST, request.FILES, sections=[section], language=language)
            if self.save_content(request, form, language):
                return HttpResponseRedirect(f"{url}?language={language}")
        else:
            form = ContentForm(sections=[section], language=language)

        return TemplateResponse(
            request,
            "admin/cms/content.html",
            self.content_context(
                request,
                title=f"{section.name} content ({language})",
                subtitle=(
                    f"Shared section, on {used_on} page(s)"
                    if section.is_library
                    else f"Section of {section.page.name}"
                ),
                content_url=url,
                sections=[section],
                form=form,
                language=language,
                extra={
                    "structure_url": reverse("admin:cms_section_change", args=(section.pk,)),
                    "back_url": reverse("admin:cms_section_changelist"),
                },
            ),
        )


@admin.register(SectionPlacement)
class SectionPlacementAdmin(StructureAdmin):
    """Which shared section sits on which page, and where."""

    list_display = ("page", "section", "order", "is_active")
    list_filter = (
        dropdown_filter("page", RelatedDropdownFilter),
        dropdown_filter("is_active", BooleanRadioFilter),
    )
    autocomplete_fields = ("page",)
    ordering = ("page", "order")
    list_select_related = ("page", "section")


@admin.register(Field)
class FieldAdmin(StructureAdmin):
    """Reachable on its own for the rare case the content screen cannot express.

    Nothing here is meant to be typed into day to day: the ``values`` box is raw
    JSON, and it is kept because a superuser occasionally needs to paste an
    import or clear a value that no longer fits its type.
    """

    list_display = ("field_header", "section", "kind", "required", "complete", "active")
    list_filter = (
        dropdown_filter("field_type", ChoicesDropdownFilter),
        dropdown_filter("section__page", RelatedDropdownFilter),
        CompletenessFilter,
        dropdown_filter("is_active", BooleanRadioFilter),
    )
    search_fields = ("name", "slug", "section__name", "section__page__name")
    ordering = ("section", "order", "name")
    autocomplete_fields = ("section",)
    readonly_fields = ("id", "created_at", "updated_at")
    list_select_related = ("section", "section__page")
    fieldsets = (
        (None, {"fields": ("id", "section", "name", "slug", "order", "help_text")}),
        (
            "Type",
            {
                "fields": ("field_type", "multiple", "options", "required"),
                "description": (
                    "The type decides the widget the content screen offers and the "
                    "shape the value is stored in. Choices are only read for a "
                    "Choice field. Changing the type keeps the content and refuses "
                    "the change if it no longer fits."
                ),
            },
        ),
        (
            "Content",
            {
                "fields": ("values", "is_active"),
                "description": (
                    "Raw JSON, keyed by language. The content screen is the place "
                    "to type this; here is for pasting an import or clearing a "
                    "value that no longer fits its type."
                ),
            },
        ),
        ("Dates", {"fields": ("created_at", "updated_at")}),
    )

    @display(description="Field", header=True)
    def field_header(self, obj: Field) -> list[str]:
        return [obj.name, obj.slug]

    @display(
        description="Type",
        # Coloured by family rather than by type, so a list of forty fields
        # reads as "copy, then numbers, then media" at a glance. Every member of
        # FieldType is here: one missing renders unlabelled, which looks like a
        # broken row rather than like a type nobody assigned a colour.
        label={
            "text": "info",
            "textarea": "info",
            "html": "info",
            "markdown": "info",
            "select": "info",
            "number": "success",
            "boolean": "success",
            "date": "warning",
            "datetime": "warning",
            "email": "warning",
            "phone": "warning",
            "url": "warning",
            "color": "warning",
            "link": "primary",
            "image": "primary",
            "video": "primary",
            "audio": "primary",
            "file": "primary",
            "page": "primary",
            "contact": "primary",
            "json": "danger",
        },
    )
    def kind(self, obj: Field) -> str:
        """The type, coloured by family, with lists marked as lists."""
        return f"{obj.field_type} (list)" if obj.multiple else obj.field_type

    @display(boolean=True, description="Filled in")
    def complete(self, obj: Field) -> bool:
        return obj.is_complete

    @display(description="Shown", boolean=True)
    def active(self, obj: Field) -> bool:
        return obj.is_active


class MenuItemInline(TabularInline):
    """The entries of a menu, parents and children in one list."""

    model = MenuItem
    extra = 0
    fields = ("label", "page", "url", "parent", "order", "new_tab", "is_active")
    ordering = ("order",)
    autocomplete_fields = ("page",)

    def formfield_for_foreignkey(self, db_field: Any, request: HttpRequest, **kwargs: Any) -> Any:
        if db_field.name == "parent":
            menu_id = (
                request.resolver_match.kwargs.get("object_id") if request.resolver_match else None
            )
            queryset = MenuItem.objects.filter(parent__isnull=True)
            kwargs["queryset"] = queryset.filter(menu_id=menu_id) if menu_id else queryset.none()
        return super().formfield_for_foreignkey(db_field, request, **kwargs)


@admin.register(Menu)
class MenuAdmin(StructureAdmin):
    """Navigation, which is content: adding a page to the header is not a deploy."""

    list_display = ("name", "slug", "entry_count", "active")
    search_fields = ("name", "slug")
    inlines = (MenuItemInline,)
    readonly_fields = ("id", "created_at", "updated_at")

    def get_queryset(self, request: HttpRequest) -> QuerySet[Menu]:
        return super().get_queryset(request).prefetch_related("items")

    @display(description="Entries")
    def entry_count(self, obj: Menu) -> int:
        return len(obj.items.all())

    @display(description="Shown", boolean=True)
    def active(self, obj: Menu) -> bool:
        return obj.is_active


@admin.register(MenuItem)
class MenuItemAdmin(StructureAdmin):
    """Reachable on its own, for moving one entry between menus."""

    list_display = ("__str__", "menu", "parent", "target", "order", "is_active")
    list_filter = (dropdown_filter("menu", RelatedDropdownFilter),)
    search_fields = ("label", "url", "page__name", "menu__name")
    autocomplete_fields = ("page",)
    ordering = ("menu", "order")
    list_select_related = ("menu", "page", "parent")

    @display(description="Points at")
    def target(self, obj: MenuItem) -> str:
        return f"/{obj.page.slug}" if obj.page_id else obj.url


@admin.register(SiteSettings)
class SiteSettingsAdmin(ModelAdmin):
    """The installation's own metadata: one row, and never a second.

    Editable by anyone who may change content, because that is what it is --
    the name and tagline are copy, not configuration.
    """

    form = SiteSettingsForm
    readonly_fields = ("updated_at",)
    warn_unsaved_form = True

    def get_form(self, request: HttpRequest, obj: Any = None, **kwargs: Any) -> Any:
        """Declare this deployment's language inputs on the form the admin builds.

        They have to be *declared* fields rather than ones added later: the
        admin lays the page out from ``base_fields``, and asks the form factory
        for exactly the names its fieldsets mention.
        """
        kwargs["form"] = type("SiteSettingsForm", (SiteSettingsForm,), site_translation_fields())
        return super().get_form(request, obj, **kwargs)

    def get_fieldsets(self, request: HttpRequest, obj: Any = None) -> Any:
        """Copy first, then branding, then the open-ended parts."""
        translated = list(site_translation_fields())

        def named(prefix: str) -> tuple[str, ...]:
            return tuple(name for name in translated if name.startswith(f"{prefix}__"))

        return (
            ("Copy", {"fields": named("name") + named("tagline")}),
            (
                "Search and sharing",
                {
                    "fields": named("description"),
                    "description": "Used for any page that does not set its own.",
                },
            ),
            (
                "Sharing (Open Graph)",
                {
                    "fields": named("og_title") + named("og_description") + ("og_image", "og_url"),
                    "description": (
                        "The card a link to this site becomes when somebody "
                        "shares it. Blank falls back to the name and the meta "
                        "description above."
                    ),
                },
            ),
            ("Branding", {"fields": ("logo", "favicon")}),
            (
                "Contact and links",
                {"fields": ("contact", "social_links"), "classes": ("collapse",)},
            ),
            ("Anything else", {"fields": ("extra", "updated_at"), "classes": ("collapse",)}),
        )

    def has_add_permission(self, request: HttpRequest) -> bool:
        return not SiteSettings.objects.exists()

    def has_delete_permission(self, request: HttpRequest, obj: Any = None) -> bool:
        return False
