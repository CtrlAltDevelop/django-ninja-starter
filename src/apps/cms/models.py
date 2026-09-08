"""Content: pages, the sections they are built from, and the typed fields inside.

This app is meant to be lifted out and dropped into any Django project, so it
owns everything it needs and asks the project for almost nothing: a settings
key it defaults for itself, and a router mount. Nothing here imports the project
around it.

The shape is three levels and no more. A *page* is something a client can ask
for by name; a *section* is a named band of it ("hero", "pricing"), optionally
holding a row of repeated children ("plan"); a *field* is one piece of content
with a type. Deeper nesting is tempting and turns an editing screen into a tree
view nobody can find anything in, so it is refused rather than discouraged.

Around those three sit the pieces a content system is incomplete without:

* a **library** of sections that belong to no page and are placed on many, so a
  footer is written once rather than copied and left to drift;
* **menus**, because navigation is content -- somebody who can add a page can
  add it to the menu without a deployment;
* **publishing**: a page is a draft until it is not, and may be dated to go live
  on its own.

Four decisions are worth stating, because each replaces something that looks
simpler and is not.

**Translations live in the row, not in extra columns.** A value is a
``{language: content}`` object. The alternative -- a column per language, which
is what ``modeltranslation`` does -- means a schema migration every time the
site adds a language, on the one table whose whole purpose is to change without
developers. Reading a language is
:func:`~apps.cms.translations.translation`, which falls back rather than
returning a hole.

**Content is validated on the way in.** ``JSONField`` will happily store
anything, and a CMS that accepts anything hands the frontend a value of an
unexpected shape at render time, in production, on the one page nobody opened
during review. So writes run ``full_clean`` -- they are rare here and always
come from a person, a fixture or an import, and validating every one of them is
cheap next to a broken page.

**A type change never silently drops content.** The obvious implementation
clears the values, because they probably no longer fit. But "probably" covers
text becoming a textarea, where they fit perfectly, and the editor who spent a
morning on that text is not consoled by the fact that the deletion was
intentional. Values are re-validated against the new type instead, and a change
that would invalidate them is refused with the language to clear first.

**Visibility is two different questions.** A section or a field is *shown or
hidden* -- one switch, reversible, used constantly. A page is *drafted,
published, or dated to publish itself* -- which is editorial state, not a
switch. Giving both the same ``is_active`` boolean would mean answering "why is
this page not live?" by checking three unrelated things.
"""

import uuid
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any, Self

from django.core.exceptions import ValidationError
from django.core.serializers.json import DjangoJSONEncoder
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.utils import timezone

from apps.cms.fields import (
    CHOICE_TYPES,
    FieldType,
    is_empty,
    normalize_options,
    normalize_value,
)
from apps.cms.translations import default_language, known_languages, translation


def _translated(values: Any, name: str) -> dict[str, Any]:
    """Validate a ``{language: value}`` mapping written by an editor."""
    if not isinstance(values, dict):
        raise ValidationError({name: "Expected an object keyed by language code."})
    languages = known_languages()
    unknown = sorted(set(values) - set(languages))
    if unknown:
        raise ValidationError(
            {
                name: (
                    f"Not a configured language: {', '.join(unknown)}. "
                    f"CMS_LANGUAGES has {', '.join(languages)}."
                )
            }
        )
    for language, value in values.items():
        if not isinstance(value, str):
            raise ValidationError({name: f"{language} must be text."})
    return values


class CmsModel(models.Model):
    """A stable id and the two dates every row here carries."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True

    def save(self, *args: Any, **kwargs: Any) -> None:
        """Validate every write, wherever it came from.

        The admin would do this anyway; an import, a shell session and a data
        migration would not, and those are exactly the writes nobody watches.
        """
        self.full_clean()
        super().save(*args, **kwargs)


class SwitchableModel(CmsModel):
    """Something an editor turns off without deleting it."""

    is_active = models.BooleanField(
        "Shown",
        default=True,
        help_text="Hidden rows stay in the admin and disappear from the API.",
    )

    class Meta:
        abstract = True


class ChangeFrequency(models.TextChoices):
    """How often a page changes, in the vocabulary sitemaps.org defines.

    A hint and never a promise: a crawler weighs it against what it has seen
    change, so the honest answer is worth more than the flattering one.
    """

    ALWAYS = "always", "Always"
    HOURLY = "hourly", "Hourly"
    DAILY = "daily", "Daily"
    WEEKLY = "weekly", "Weekly"
    MONTHLY = "monthly", "Monthly"
    YEARLY = "yearly", "Yearly"
    NEVER = "never", "Never"


class SiteSettings(models.Model):
    """The one row describing whatever this content belongs to.

    A singleton rather than a settings module, because everything on it is
    copy: the people who change a tagline are the people who write it, and they
    do not deploy.
    """

    SINGLETON_ID = 1

    id = models.PositiveSmallIntegerField(primary_key=True, default=SINGLETON_ID, editable=False)
    # blank, because an installation with no content yet is a legitimate
    # state -- and it is the one every fresh database starts in.
    name = models.JSONField(
        default=dict, blank=True, help_text='Name per language: {"en-us": "..."}.'
    )
    tagline = models.JSONField(default=dict, blank=True, help_text="Short line under the name.")
    description = models.JSONField(default=dict, blank=True, help_text="Default meta description.")
    logo = models.URLField(blank=True)
    favicon = models.URLField(blank=True)
    # The four Open Graph values, which is what "SEO metadata" now means: they
    # decide what a link to this site looks like in a chat window, a timeline or
    # a search result card. `og_title` and `og_description` are separate from
    # `name` and `description` because the good version of each is different --
    # a page title is read next to the site's chrome, a shared card is read on
    # its own -- and blank simply falls back to the plainer one.
    og_title = models.JSONField(
        "Social title", default=dict, blank=True, help_text="Falls back to the site name."
    )
    og_description = models.JSONField(
        "Social description",
        default=dict,
        blank=True,
        help_text="Falls back to the meta description.",
    )
    og_image = models.URLField("Social preview image", blank=True)
    og_url = models.URLField(
        "Canonical URL",
        blank=True,
        help_text="The site's own address, for any page that does not set one.",
    )
    # The sitemap, which is the one piece of SEO nobody can write by hand and
    # keep right: it has to list what is live *now*, and what is live now
    # changes every time somebody publishes. So it is generated, and what an
    # editor owns here is the handful of judgements a generator cannot make --
    # whether to publish one at all, what address the pages actually have, and
    # how often a crawler should bother coming back.
    sitemap_enabled = models.BooleanField(
        "Publish a sitemap",
        default=True,
        help_text="Turn off to answer the sitemap URL with a 404, for a site behind a login.",
    )
    sitemap_base_url = models.URLField(
        "Sitemap base URL",
        blank=True,
        help_text=(
            "The address pages hang off, for example https://example.com. "
            "Blank uses the canonical URL above."
        ),
    )
    sitemap_changefreq = models.CharField(
        "Default change frequency",
        max_length=16,
        choices=ChangeFrequency.choices,
        default=ChangeFrequency.WEEKLY,
        help_text="Used for any page that does not set its own.",
    )
    sitemap_priority = models.DecimalField(
        "Default priority",
        max_digits=2,
        decimal_places=1,
        default=Decimal("0.5"),
        validators=(MinValueValidator(Decimal("0.0")), MaxValueValidator(Decimal("1.0"))),
        help_text="Between 0.0 and 1.0, and only ever compared with this site's own pages.",
    )
    contact = models.JSONField(
        default=dict, blank=True, help_text='Free-form contact details: {"email": "..."}.'
    )
    social_links = models.JSONField(
        default=list, blank=True, help_text='[{"label": "X", "url": "https://..."}]'
    )
    extra = models.JSONField(
        default=dict, blank=True, help_text="Anything else every client needs."
    )
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Site settings"
        verbose_name_plural = "Site settings"

    def __str__(self) -> str:
        return translation(self.name, default_language()) or "Site settings"

    def save(self, *args: Any, **kwargs: Any) -> None:
        """Always the same row: a second one would be a silent second site."""
        self.pk = self.SINGLETON_ID
        # The id is excluded because it is always taken: this row is the site,
        # and a fresh instance means "this is the site now", not "here is a
        # second one" -- so it updates the existing row instead of colliding
        # with it.
        self.full_clean(exclude=("id",))
        if self._state.adding and type(self).objects.filter(pk=self.pk).exists():
            self._state.adding = False
            kwargs["force_insert"] = False
        super().save(*args, **kwargs)

    @classmethod
    def load(cls) -> Self:
        """The row, or an unsaved default one, so a read never writes."""
        return cls.objects.filter(pk=cls.SINGLETON_ID).first() or cls()

    @classmethod
    async def aload(cls) -> Self:
        """:meth:`load` for the async read endpoints."""
        return (await cls.objects.filter(pk=cls.SINGLETON_ID).afirst()) or cls()

    def clean(self) -> None:
        super().clean()
        self.name = _translated(self.name, "name")
        self.tagline = _translated(self.tagline, "tagline")
        self.description = _translated(self.description, "description")
        self.og_title = _translated(self.og_title, "og_title")
        self.og_description = _translated(self.og_description, "og_description")
        if not isinstance(self.contact, dict) or not all(
            isinstance(value, str) for value in self.contact.values()
        ):
            raise ValidationError({"contact": "Expected an object of text values."})
        if not isinstance(self.social_links, list) or not all(
            isinstance(item, dict) and "url" in item for item in self.social_links
        ):
            raise ValidationError({"social_links": "Expected a list of objects, each with a url."})
        if not isinstance(self.extra, dict):
            raise ValidationError({"extra": "Expected an object."})


class PageStatus(models.TextChoices):
    DRAFT = "draft", "Draft"
    PUBLISHED = "published", "Published"


class PageQuerySet(models.QuerySet["Page"]):
    def live(self, at: datetime | None = None) -> "PageQuerySet":
        """Published, and not waiting for a date that has not arrived."""
        moment = at or timezone.now()
        return self.filter(status=PageStatus.PUBLISHED).filter(
            models.Q(published_at__isnull=True) | models.Q(published_at__lte=moment)
        )


class Page(CmsModel):
    """Something a client can ask for by name.

    Publishing is a state and a date rather than a switch, because those are the
    two questions an editor actually has: is this ready, and when should it
    appear. A page dated in the future is published and simply not live yet,
    which is a sentence somebody can act on.
    """

    name = models.CharField("Page name", max_length=80, help_text="What editors call this page.")
    slug = models.SlugField(
        max_length=80,
        unique=True,
        help_text="The name the API is asked for, for example home or about-us.",
    )
    order = models.PositiveIntegerField(default=0, help_text="Order in a generated menu.")
    status = models.CharField(
        max_length=16,
        choices=PageStatus.choices,
        default=PageStatus.DRAFT,
        help_text="A draft is invisible to the API and readable through a preview link.",
    )
    published_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="Leave empty to go live as soon as it is published, or set a date to wait for.",
    )
    title = models.JSONField(default=dict, blank=True, help_text="Meta title per language.")
    description = models.JSONField(
        default=dict, blank=True, help_text="Meta description per language."
    )
    # Open Graph, per page, falling back to the site's. All four, rather than
    # only the image: a card with the right picture and the site's generic
    # heading under it is the commonest way a shared link undersells the page it
    # points at, and `og_url` is what stops three addresses for one page being
    # counted as three pages.
    og_title = models.JSONField(
        "Social title", default=dict, blank=True, help_text="Falls back to the meta title."
    )
    og_description = models.JSONField(
        "Social description",
        default=dict,
        blank=True,
        help_text="Falls back to the meta description.",
    )
    og_image = models.URLField("Social preview image", blank=True)
    og_url = models.URLField(
        "Canonical URL", blank=True, help_text="Where this page really lives, if not here."
    )

    # Per page, and all three optional: the sitemap works with none of them
    # filled in, and each is here for the page that is an exception. Excluding
    # one is the common case -- a thank-you page or a landing page for one
    # campaign is live, reachable and has no business in a search index.
    in_sitemap = models.BooleanField(
        "List in the sitemap",
        default=True,
        help_text="Turn off for a page that is live but should not be indexed.",
    )
    sitemap_changefreq = models.CharField(
        "Change frequency",
        max_length=16,
        blank=True,
        choices=ChangeFrequency.choices,
        help_text="Leave empty to use the site's default.",
    )
    sitemap_priority = models.DecimalField(
        "Priority",
        max_digits=2,
        decimal_places=1,
        null=True,
        blank=True,
        validators=(MinValueValidator(Decimal("0.0")), MaxValueValidator(Decimal("1.0"))),
        help_text="Between 0.0 and 1.0. Leave empty to use the site's default.",
    )

    objects = PageQuerySet.as_manager()

    class Meta:
        verbose_name = "Page"
        verbose_name_plural = "Pages"
        ordering = ("order", "name")
        indexes = (models.Index(fields=("status", "published_at")),)

    def __str__(self) -> str:
        return self.name

    def clean(self) -> None:
        super().clean()
        self.title = _translated(self.title, "title")
        self.description = _translated(self.description, "description")
        self.og_title = _translated(self.og_title, "og_title")
        self.og_description = _translated(self.og_description, "og_description")

    @property
    def is_live(self) -> bool:
        """Whether a client asking for this page right now would be given it."""
        if self.status != PageStatus.PUBLISHED:
            return False
        return self.published_at is None or self.published_at <= timezone.now()

    @property
    def is_scheduled(self) -> bool:
        """Published, with a date that has not arrived."""
        return self.status == PageStatus.PUBLISHED and not self.is_live

    def publish(self, at: datetime | None = None) -> None:
        """Publish now, or on a date. Saving is the caller's business."""
        self.status = PageStatus.PUBLISHED
        self.published_at = at

    def unpublish(self) -> None:
        self.status = PageStatus.DRAFT


class Section(SwitchableModel):
    """A named band of one page -- or of none, which makes it reusable.

    A section with no page is a *library* section: written once and placed on as
    many pages as want it, which is what a footer or a call-to-action actually
    is. Copying one onto each page instead is the thing that leaves five
    almost-identical footers behind.

    ``parent`` exists for the repeated case: a pricing section holding three
    plans, a team section holding people. Anything deeper is a layout problem
    being solved in the content model.
    """

    page = models.ForeignKey(
        Page,
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="sections",
        help_text="Leave empty to put this section in the library, for use on many pages.",
    )
    parent = models.ForeignKey(
        "self",
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="children",
        help_text="Leave empty for a top-level section. Nesting is one level deep.",
    )
    name = models.CharField("Section name", max_length=200)
    slug = models.SlugField(
        max_length=200, help_text="The key the client matches on, for example hero."
    )
    order = models.PositiveIntegerField(default=0)

    class Meta:
        verbose_name = "Section"
        verbose_name_plural = "Sections"
        ordering = ("order", "name")
        constraints = (
            models.UniqueConstraint(fields=("page", "slug"), name="unique_page_section_slug"),
            # NULLs do not collide in SQL, so the constraint above says nothing
            # at all about the library. This one does.
            models.UniqueConstraint(
                fields=("slug",),
                condition=models.Q(page__isnull=True),
                name="unique_library_section_slug",
            ),
        )
        indexes = (
            models.Index(fields=("page", "slug")),
            models.Index(fields=("page", "order")),
        )

    def __str__(self) -> str:
        return f"{self.page.name} - {self.name}" if self.page_id else f"Library - {self.name}"

    @property
    def is_library(self) -> bool:
        return self.page_id is None

    def clean(self) -> None:
        super().clean()
        if self.parent_id is None:
            return
        if self.parent_id == self.pk:
            raise ValidationError({"parent": "A section cannot be its own parent."})
        parent = self.parent
        if parent.parent_id is not None:
            raise ValidationError({"parent": "Sections nest one level deep, no further."})
        if parent.page_id != self.page_id:
            # Without this the section would be unreachable: every read starts
            # from a page and walks down, so a child of another page's section
            # is content nobody can ever fetch.
            raise ValidationError(
                {"parent": "The parent section belongs to a different page or to the library."}
            )


class SectionPlacement(SwitchableModel):
    """One library section, put on one page, at one position.

    The position lives here rather than on the section because it is a fact
    about this page: the same footer is last on every page, but the same
    call-to-action might sit above the pricing on one and below it on another.
    """

    page = models.ForeignKey(Page, on_delete=models.CASCADE, related_name="placements")
    section = models.ForeignKey(
        Section,
        on_delete=models.CASCADE,
        related_name="placements",
        limit_choices_to={"page__isnull": True},
        help_text="Only library sections -- ones that belong to no page -- can be placed.",
    )
    order = models.PositiveIntegerField(
        default=0, help_text="Sorted with the page's own sections, in one list."
    )

    class Meta:
        verbose_name = "Shared section"
        verbose_name_plural = "Shared sections"
        ordering = ("page", "order")
        constraints = (
            models.UniqueConstraint(fields=("page", "section"), name="unique_page_placement"),
        )

    def __str__(self) -> str:
        return f"{self.page.name} - {self.section.name}"

    def clean(self) -> None:
        super().clean()
        if self.section_id and self.section.page_id is not None:
            raise ValidationError(
                {"section": "Only a library section can be placed. This one belongs to a page."}
            )


class Field(SwitchableModel):
    """One piece of content, typed, in as many languages as have been written.

    ``field_type`` and ``multiple`` are the structure a developer designs;
    ``values`` is what an editor fills in. Keeping both on one row is what makes
    the admin able to render the right widget for each without a schema.
    """

    section = models.ForeignKey(Section, on_delete=models.CASCADE, related_name="fields")
    name = models.CharField("Label", max_length=200, help_text="Shown to editors, not to readers.")
    slug = models.SlugField(
        max_length=200, help_text="The key the client reads, for example headline."
    )
    help_text = models.CharField(
        max_length=300, blank=True, help_text="Guidance for whoever fills this in."
    )
    field_type = models.CharField(
        "Type", max_length=20, choices=FieldType.choices, default=FieldType.TEXT
    )
    multiple = models.BooleanField(
        default=False, help_text="Store a list of these instead of a single one."
    )
    options = models.JSONField(
        "Choices",
        default=list,
        blank=True,
        help_text=(
            'Only for a Choice field: ["small", "large"], or '
            '[{"value": "sm", "label": "Small"}] to store one word and show another.'
        ),
    )
    required = models.BooleanField(
        default=False,
        help_text="The editing screen insists on this in the default language.",
    )
    order = models.PositiveIntegerField(default=0)
    values = models.JSONField(
        default=dict,
        blank=True,
        encoder=DjangoJSONEncoder,
        help_text="Content per language code. Validated against the type above.",
    )

    class Meta:
        verbose_name = "Field"
        verbose_name_plural = "Fields"
        ordering = ("order", "name")
        constraints = (
            models.UniqueConstraint(fields=("section", "slug"), name="unique_section_field_slug"),
        )
        indexes = (
            models.Index(fields=("section", "slug")),
            models.Index(fields=("section", "order")),
        )

    def __str__(self) -> str:
        return f"{self.section.name} - {self.name}"

    def clean(self) -> None:
        super().clean()
        try:
            self.options = normalize_options(self.options)
        except ValidationError as error:
            raise ValidationError({"options": error.messages}) from error
        if self.options and self.field_type not in CHOICE_TYPES:
            # Not silently dropped: an editor who typed choices onto a text
            # field meant to make it a dropdown, and clearing the box for them
            # would look like the save worked.
            raise ValidationError(
                {"options": "Only a Choice field has choices. Change the type, or clear these."}
            )

        if not isinstance(self.values, dict):
            raise ValidationError({"values": "Expected an object keyed by language code."})
        languages = known_languages()
        unknown = sorted(set(self.values) - set(languages))
        if unknown:
            raise ValidationError(
                {
                    "values": (
                        f"Not a configured language: {', '.join(unknown)}. "
                        f"CMS_LANGUAGES has {', '.join(languages)}."
                    )
                }
            )

        cleaned: dict[str, Any] = {}
        for language, value in self.values.items():
            if is_empty(value):
                continue
            try:
                cleaned[language] = normalize_value(
                    self.field_type, value, multiple=self.multiple, options=self.options
                )
            except ValidationError as error:
                raise ValidationError(
                    {"values": f"{language}: {'; '.join(error.messages)}"}
                ) from error
        self.values = cleaned

    @property
    def is_complete(self) -> bool:
        """Whether a required field has the content it says it needs.

        Deliberately a question and not a constraint. ``required`` is designed
        into the structure, and a structure is designed before anybody has
        written a word -- so enforcing it on the row would make declaring a
        required field impossible. The editing screen asks it instead, at the
        point where somebody is in a position to answer.
        """
        return not self.required or default_language() in self.values

    def value_for(self, language: str) -> Any:
        """The content a reader of this language should see, or ``None``."""
        return translation(self.values, language)

    @property
    def choices(self) -> list[tuple[str, str]]:
        """This field's options as Django's ``(value, label)`` pairs, for a dropdown."""
        return [(option["value"], option["label"]) for option in normalize_options(self.options)]

    @property
    def written_languages(self) -> list[str]:
        """The configured languages somebody has actually filled this in for.

        In configured order rather than in whatever order the JSON object
        happens to have, so two fields' answers to this line up on screen.
        """
        return [language for language in known_languages() if language in self.values]


class Menu(SwitchableModel):
    """A named list of links: navigation is content, not routing.

    Without this, adding a page to the header is a frontend deployment -- which
    is the thing a content system exists to avoid.
    """

    name = models.CharField(max_length=80)
    slug = models.SlugField(
        max_length=80, unique=True, help_text="What the API is asked for, for example main."
    )

    class Meta:
        verbose_name = "Menu"
        verbose_name_plural = "Menus"
        ordering = ("name",)

    def __str__(self) -> str:
        return self.name


class MenuItem(SwitchableModel):
    """One entry in a menu: a page, or any other address.

    A link to a page is stored as the page, not as its address, so renaming the
    page's slug moves the menu with it rather than leaving a dead entry behind.
    """

    menu = models.ForeignKey(Menu, on_delete=models.CASCADE, related_name="items")
    parent = models.ForeignKey(
        "self",
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="children",
        help_text="Leave empty for a top-level entry. Nesting is one level deep.",
    )
    label = models.JSONField(
        default=dict, blank=True, help_text='Text per language: {"en-us": "About us"}.'
    )
    page = models.ForeignKey(
        Page,
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="menu_items",
        help_text="Link to a page in this CMS. Leave empty and fill in a URL instead.",
    )
    url = models.CharField(
        max_length=500, blank=True, help_text="Any other address: a path, or an external URL."
    )
    new_tab = models.BooleanField(default=False)
    order = models.PositiveIntegerField(default=0)

    class Meta:
        verbose_name = "Menu item"
        verbose_name_plural = "Menu items"
        ordering = ("menu", "order")
        indexes = (models.Index(fields=("menu", "order")),)

    def __str__(self) -> str:
        return translation(self.label, default_language()) or "Menu item"

    def clean(self) -> None:
        super().clean()
        self.label = _translated(self.label, "label")
        if not self.label:
            raise ValidationError({"label": "Give this entry a label in at least one language."})
        if self.page_id and self.url:
            raise ValidationError({"url": "Choose a page or a URL, not both."})
        if not self.page_id and not self.url:
            raise ValidationError({"url": "Choose a page, or type a URL."})
        if self.parent_id is None:
            return
        if self.parent_id == self.pk:
            raise ValidationError({"parent": "An entry cannot be its own parent."})
        parent = self.parent
        if parent.parent_id is not None:
            raise ValidationError({"parent": "Menus nest one level deep, no further."})
        if parent.menu_id != self.menu_id:
            raise ValidationError({"parent": "The parent entry belongs to a different menu."})


class SiteEventKind(models.TextChoices):
    """What kind of thing the date is, because it decides who reacts to it."""

    RENEWAL = "renewal", "Renewal"
    CAMPAIGN = "campaign", "Campaign"
    DEADLINE = "deadline", "Deadline"
    CONTENT = "content", "Content refresh"
    OTHER = "other", "Other"


class SiteEventQuerySet(models.QuerySet["SiteEvent"]):
    def watched(self) -> "SiteEventQuerySet":
        """The events still being kept an eye on.

        Ticked-off ones stay in: a yearly event that was handled last year is
        handled and due again, and only :meth:`SiteEvent.is_due` -- which knows
        which occurrence the tick was for -- can tell the two apart.
        """
        return self.filter(is_active=True)


class SiteEvent(SwitchableModel):
    """A date somebody running this site has to do something about.

    Not content: nothing here is ever served to a reader. It is the other half
    of running a site -- the domain that expires, the campaign that starts on
    the first, the price list that has to be checked before the new year -- and
    the reason it is a model rather than a calendar invitation is that the
    person who has to act on it is looking at this admin, not at that calendar.

    A reminder is a *lead time* rather than a second date. "Tell me two weeks
    before" survives the date moving; "remind me on the 14th" does not, and the
    date moves constantly.

    ``repeats_yearly`` is here because most of these dates are annual by nature
    -- renewals, audits, the January price review -- and the alternative is a
    row that goes stale the day after it fires and is either deleted, losing the
    history, or left to nag forever.
    """

    name = models.CharField("Event", max_length=200, help_text="What has to happen.")
    kind = models.CharField(
        max_length=16, choices=SiteEventKind.choices, default=SiteEventKind.OTHER
    )
    happens_on = models.DateField(
        "Date", help_text="The day it happens, or the deadline it has to be done by."
    )
    repeats_yearly = models.BooleanField(
        default=False, help_text="For a date that comes round every year, like a renewal."
    )
    remind_days_before = models.PositiveSmallIntegerField(
        "Remind this many days before",
        default=14,
        help_text="How much warning the dashboard gives. Zero means on the day.",
    )
    notes = models.TextField(blank=True, help_text="What whoever picks this up needs to know.")
    url = models.URLField(blank=True, help_text="Where the work gets done: a registrar, a doc.")
    is_done = models.BooleanField(
        "Handled",
        default=False,
        help_text="Ticked off. A yearly event comes back on its own once the date has passed.",
    )
    # Which occurrence the tick above refers to. Without it, ticking off this
    # year's domain renewal would silence next year's too -- and the reminder
    # nobody sees is the one for the renewal that lapses.
    handled_occurrence = models.DateField(null=True, blank=True, editable=False)

    objects = SiteEventQuerySet.as_manager()

    class Meta:
        verbose_name = "Site event"
        verbose_name_plural = "Site events"
        ordering = ("happens_on", "name")
        indexes = (models.Index(fields=("is_done", "happens_on")),)

    def __str__(self) -> str:
        return f"{self.name} ({self.happens_on:%Y-%m-%d})"

    def clean(self) -> None:
        super().clean()
        if self.repeats_yearly and self.remind_days_before > 365:
            raise ValidationError(
                {"remind_days_before": "A yearly event cannot be announced more than a year out."}
            )
        # Recorded on the way in rather than by the screen that ticked the box,
        # because an admin action, a fixture and a shell session all tick it.
        self.handled_occurrence = self.next_date() if self.is_done else None

    def next_date(self, today: date | None = None) -> date:
        """The occurrence being counted down to.

        For a one-off that is simply its date, past or not: an overdue renewal
        must keep saying it is overdue rather than disappearing. For a yearly
        one it is this year's, or next year's once this year's has gone by.
        """
        moment = today or timezone.localdate()
        if not self.repeats_yearly or self.happens_on >= moment:
            return self.happens_on
        try:
            this_year = self.happens_on.replace(year=moment.year)
        except ValueError:
            # 29 February in a year that has none. The 28th is what everybody
            # means by "the same day", and is what every calendar app does.
            this_year = self.happens_on.replace(year=moment.year, day=28)
        return this_year if this_year >= moment else this_year.replace(year=moment.year + 1)

    def days_away(self, today: date | None = None) -> int:
        """Days until the next occurrence. Negative once it has gone by."""
        return (self.next_date(today) - (today or timezone.localdate())).days

    def is_overdue(self, today: date | None = None) -> bool:
        return self.days_away(today) < 0

    def is_due(self, today: date | None = None) -> bool:
        """Whether the dashboard should be showing this now.

        Overdue counts as due. The whole point of the reminder is the thing that
        did not get done, and a list that drops a date the moment it passes is a
        list that is empty exactly when it matters.
        """
        if self.is_done and self.handled_occurrence == self.next_date(today):
            return False
        return self.days_away(today) <= self.remind_days_before

    @property
    def reminder_starts_on(self) -> date:
        """The day this begins appearing on the dashboard."""
        return self.next_date() - timedelta(days=self.remind_days_before)


def due_events(today: date | None = None) -> list[SiteEvent]:
    """Every event whose reminder window is open, soonest first.

    Sorted and filtered in Python rather than in SQL, and deliberately: a yearly
    event's next occurrence is a calculation over the row, not a column, and
    expressing it in the database would take a ``CASE`` over three date parts to
    order a table that holds a dozen rows. This is the cheap half of that trade.
    """
    moment = today or timezone.localdate()
    due = [event for event in SiteEvent.objects.watched() if event.is_due(moment)]
    return sorted(due, key=lambda event: (event.next_date(moment), event.name))
