"""The admin: working a queue, and the things only an administrator sets up.

Two audiences, and the screens are built for whichever one is looking.

**An agent working the queue** wants the ticket list to answer "what should I
pick up next" without opening anything: who it is from, how long it has been
waiting, whether it is in breach, and whether anybody has it. So the list is
mostly computed columns, the filters are the questions an agent actually asks,
and the thread itself is an inline underneath the ticket rather than a separate
model to go and find.

**An administrator setting the desk up** edits categories, tags and canned
replies, which are three small screens that are edited once and then left
alone.

Two things here are read-only on purpose, and for the same reason: they are
records of something that happened. **Participants** carry read watermarks, and
a watermark somebody can type in is a watermark that proves nothing.
**Attachments** are what a message said at the time. Both are visible and
neither is editable.

A message *is* editable in the admin, and only its body -- because an
administrator sometimes has to redact a credit-card number a client pasted into
a support ticket, and that is a real job. It leaves an ``edited_at``, exactly as
an edit over the API does, so the thread never silently changes underneath the
people reading it.

The theme is resolved the way the CMS and the notifications app resolve theirs:
Unfold where the project installs it, Django's own admin where it does not, so
this app can be lifted into a project that has never heard of either.
"""

from typing import Any

from django.contrib import admin
from django.db.models import Count, Q, QuerySet
from django.http import HttpRequest
from django.utils import timezone
from django.utils.html import format_html

from apps.support.models import (
    LIVE_STATUSES,
    Attachment,
    CannedReply,
    Category,
    Message,
    Participant,
    Priority,
    Status,
    Tag,
    Ticket,
    Upload,
)

try:  # pragma: no cover - exercised by whichever branch the project installs
    from unfold.admin import ModelAdmin, StackedInline, TabularInline
except ImportError:  # pragma: no cover - only in a project without Unfold
    from django.contrib.admin import (
        ModelAdmin,
        StackedInline,
        TabularInline,
    )

STATUS_COLOURS = {
    Status.OPEN: "#dc2626",
    Status.PENDING: "#d97706",
    Status.ON_HOLD: "#6b7280",
    Status.RESOLVED: "#16a34a",
    Status.CLOSED: "#2563eb",
}

PRIORITY_COLOURS = {
    Priority.LOW: "#6b7280",
    Priority.NORMAL: "#2563eb",
    Priority.HIGH: "#d97706",
    Priority.URGENT: "#dc2626",
}


def _badge(colour: str, label: str) -> Any:
    return format_html('<span style="color: {}; font-weight: 600">{}</span>', colour, label)


def _age(moment: Any) -> str:
    """How long ago something was, in the coarsest unit that is still true.

    "3 days" rather than "3 days, 4:12:07". An agent scanning a queue is
    comparing magnitudes, and the seconds are noise that makes the column wider
    and the comparison slower.
    """
    if moment is None:
        return "--"
    delta = timezone.now() - moment
    if delta.days >= 1:
        return f"{delta.days} day{'' if delta.days == 1 else 's'}"
    hours = delta.seconds // 3600
    if hours:
        return f"{hours} hour{'' if hours == 1 else 's'}"
    minutes = max(delta.seconds // 60, 0)
    return f"{minutes} min"


def _until(moment: Any) -> str:
    """How long there is left, in the same coarse units as :func:`_age`.

    A separate function rather than a signed version of one, because "3 days
    ago" and "in 3 days" are the two things an agent must never read as each
    other, and a minus sign in a table column is not enough of a difference.
    """
    if moment is None:
        return "--"
    delta = moment - timezone.now()
    if delta.total_seconds() <= 0:
        return "now"
    if delta.days >= 1:
        return f"{delta.days} day{'' if delta.days == 1 else 's'}"
    hours = delta.seconds // 3600
    if hours:
        return f"{hours} hour{'' if hours == 1 else 's'}"
    return f"{max(delta.seconds // 60, 0)} min"


class MessageInline(StackedInline):
    """The conversation, underneath the ticket it belongs to.

    Stacked rather than tabular because the body is a paragraph, and a tabular
    inline renders a paragraph as an unreadable sliver. Everything but the body
    is read-only: an administrator may redact what was said, and may not rewrite
    who said it or when.
    """

    model = Message
    extra = 0
    fields = ("created_at", "author", "kind", "visibility", "body", "edited_at", "deleted_at")
    readonly_fields = ("created_at", "author", "kind", "visibility", "edited_at", "deleted_at")
    ordering = ("created_at",)

    def get_queryset(self, request: HttpRequest) -> QuerySet[Message]:
        return (
            super().get_queryset(request).select_related("author").prefetch_related("attachments")
        )

    def has_add_permission(self, request: HttpRequest, obj: Any = None) -> bool:
        """No. A message typed into the admin would have no author and no delivery.

        Replying is what the API and the socket are for -- they attribute the
        message to whoever sent it and push it to the client. An administrator
        who wants to answer a ticket should answer it as themselves.
        """
        return False


class ParticipantInline(TabularInline):
    """Who is in the conversation and how far through it they are. A record, so read-only."""

    model = Participant
    extra = 0
    fields = ("user", "role", "joined_at", "last_read_at", "notify")
    readonly_fields = ("user", "role", "joined_at", "last_read_at")

    def get_queryset(self, request: HttpRequest) -> QuerySet[Participant]:
        return super().get_queryset(request).select_related("user")

    def has_add_permission(self, request: HttpRequest, obj: Any = None) -> bool:
        return False


@admin.register(Ticket)
class TicketAdmin(ModelAdmin):
    """The queue, and one conversation with everything about it on one page."""

    list_display = (
        "reference",
        "subject_or_kind",
        "client",
        "status_badge",
        "priority_badge",
        "assignee",
        "waiting",
        "sla_state",
        "messages",
    )
    list_filter = ("status", "priority", "kind", "category", "tags", "created_at")
    list_select_related = ("client", "assignee", "category")
    search_fields = (
        "reference",
        "subject",
        "messages__body",
        "client__username",
        "client__email",
    )
    autocomplete_fields = ("client", "assignee", "category", "tags")
    date_hierarchy = "created_at"
    inlines = (MessageInline, ParticipantInline)
    actions = ("mark_resolved", "mark_closed", "unassign")
    readonly_fields = (
        "id",
        "reference",
        "created_at",
        "updated_at",
        "last_message_at",
        "first_response_at",
        "resolved_at",
        "closed_at",
        "first_response_due_at",
        "resolution_due_at",
        "rated_at",
        "sla_state",
    )
    fieldsets = (
        (None, {"fields": ("reference", "kind", "client", "subject", "category")}),
        ("Handling", {"fields": ("status", "priority", "assignee", "tags")}),
        (
            "Service level",
            {
                "description": (
                    "Copied from the category when the ticket was opened, so editing a "
                    "category does not move the deadline of a ticket already filed "
                    "under it."
                ),
                "fields": (
                    "first_response_due_at",
                    "resolution_due_at",
                    "first_response_at",
                    "sla_state",
                ),
            },
        ),
        (
            "What the client thought",
            {"classes": ("collapse",), "fields": ("rating", "rating_comment", "rated_at")},
        ),
        (
            "Payload",
            {
                "classes": ("collapse",),
                "description": "Whatever the client that opened this attached. Sent verbatim.",
                "fields": ("data",),
            },
        ),
        (
            "Record",
            {
                "classes": ("collapse",),
                "fields": (
                    "id",
                    "created_at",
                    "updated_at",
                    "last_message_at",
                    "resolved_at",
                    "closed_at",
                ),
            },
        ),
    )

    def get_queryset(self, request: HttpRequest) -> QuerySet[Ticket]:
        """Count the messages in the list query rather than once per row."""
        return (
            super()
            .get_queryset(request)
            .select_related("client", "assignee", "category")
            .annotate(message_count=Count("messages", filter=Q(messages__deleted_at__isnull=True)))
        )

    @admin.display(description="Subject", ordering="subject")
    def subject_or_kind(self, ticket: Ticket) -> str:
        """A chat usually has no subject, so say what it is instead of nothing."""
        return ticket.subject or f"({ticket.get_kind_display()})"

    @admin.display(description="Status", ordering="status")
    def status_badge(self, ticket: Ticket) -> Any:
        return _badge(STATUS_COLOURS.get(ticket.status, "#6b7280"), ticket.get_status_display())

    @admin.display(description="Priority", ordering="priority")
    def priority_badge(self, ticket: Ticket) -> Any:
        return _badge(
            PRIORITY_COLOURS.get(ticket.priority, "#6b7280"), ticket.get_priority_display()
        )

    @admin.display(description="Waiting", ordering="last_message_at")
    def waiting(self, ticket: Ticket) -> str:
        """How long since anything happened. The column a queue is really sorted by."""
        if ticket.is_settled:
            return "--"
        return _age(ticket.last_message_at or ticket.created_at)

    @admin.display(description="SLA")
    def sla_state(self, ticket: Ticket) -> Any:
        """Whether a promise was made, and whether it was kept.

        Not sortable, because it is not a column: a breach is computed against
        the clock. Sorting the queue by ``first_response_due_at`` is the
        orderable version of the same question and is one click away in the list
        filter.
        """
        if ticket.first_response_due_at is None and ticket.resolution_due_at is None:
            return "--"
        if ticket.breached:
            missed = []
            if ticket.first_response_breached:
                missed.append("first reply")
            if ticket.resolution_breached:
                missed.append("resolution")
            return _badge("#dc2626", f"Missed {' and '.join(missed)}")
        if ticket.awaiting_first_response and ticket.first_response_due_at:
            return _badge("#d97706", f"Reply due in {_until(ticket.first_response_due_at)}")
        return _badge("#16a34a", "On time")

    @admin.display(description="Messages", ordering="message_count")
    def messages(self, ticket: Ticket) -> int:
        return getattr(ticket, "message_count", 0)

    # -- actions ----------------------------------------------------------
    #
    # Deliberately only the three that are safe to do to a hundred rows at once.
    # Assigning in bulk is not here: giving one agent everything selected is
    # rarely what somebody meant and is not undoable from this screen.

    @admin.action(description="Mark selected resolved")
    def mark_resolved(self, request: HttpRequest, queryset: QuerySet[Ticket]) -> None:
        self._settle(request, queryset, str(Status.RESOLVED))

    @admin.action(description="Mark selected closed")
    def mark_closed(self, request: HttpRequest, queryset: QuerySet[Ticket]) -> None:
        self._settle(request, queryset, str(Status.CLOSED))

    def _settle(self, request: HttpRequest, queryset: QuerySet[Ticket], status: str) -> None:
        """Save each row rather than calling ``update``.

        ``update`` skips ``post_save``, so every client watching one of these
        threads would be left showing it as open forever. A loop is the price of
        the broadcast, and a bulk action over enough rows for it to hurt is one
        somebody should be doing with a management command.
        """
        from apps.support.models import set_status

        changed = 0
        for ticket in queryset:
            changed += bool(set_status(ticket, status))
        self.message_user(request, f"{changed} ticket{'' if changed == 1 else 's'} updated.")

    @admin.action(description="Return selected to the unassigned queue")
    def unassign(self, request: HttpRequest, queryset: QuerySet[Ticket]) -> None:
        from apps.support.models import assign

        changed = sum(bool(assign(ticket, None)) for ticket in queryset)
        self.message_user(request, f"{changed} ticket{'' if changed == 1 else 's'} unassigned.")


@admin.register(Category)
class CategoryAdmin(ModelAdmin):
    """What a ticket can be about, and the promise the desk makes about each."""

    list_display = (
        "name",
        "slug",
        "default_priority",
        "first_response_promise",
        "resolution_promise",
        "is_active",
        "tickets",
    )
    list_filter = ("is_active", "default_priority")
    search_fields = ("name", "slug", "description")
    prepopulated_fields = {"slug": ("name",)}
    ordering = ("order", "name")
    fieldsets = (
        (None, {"fields": ("name", "slug", "description", "order", "is_active")}),
        (
            "What the desk promises",
            {
                "description": (
                    "Zero means no promise, which is not the same as a promise of zero "
                    "minutes. A ticket copies these when it is opened, so changing them "
                    "does not move the deadlines of tickets already filed."
                ),
                "fields": ("default_priority", "first_response_minutes", "resolution_minutes"),
            },
        ),
    )

    def get_queryset(self, request: HttpRequest) -> QuerySet[Category]:
        return super().get_queryset(request).annotate(ticket_count=Count("tickets"))

    @admin.display(description="First reply within", ordering="first_response_minutes")
    def first_response_promise(self, category: Category) -> str:
        return _minutes(category.first_response_minutes)

    @admin.display(description="Resolved within", ordering="resolution_minutes")
    def resolution_promise(self, category: Category) -> str:
        return _minutes(category.resolution_minutes)

    @admin.display(description="Tickets", ordering="ticket_count")
    def tickets(self, category: Category) -> int:
        return getattr(category, "ticket_count", 0)


def _minutes(value: int) -> str:
    """A promise in the unit a person would say it in."""
    if not value:
        return "no promise"
    if value % (60 * 24) == 0:
        days = value // (60 * 24)
        return f"{days} day{'' if days == 1 else 's'}"
    if value % 60 == 0:
        hours = value // 60
        return f"{hours} hour{'' if hours == 1 else 's'}"
    return f"{value} min"


@admin.register(Tag)
class TagAdmin(ModelAdmin):
    """The desk's own vocabulary. Never shown to a client."""

    list_display = ("name", "slug", "swatch", "tickets")
    search_fields = ("name", "slug")
    prepopulated_fields = {"slug": ("name",)}

    def get_queryset(self, request: HttpRequest) -> QuerySet[Tag]:
        return super().get_queryset(request).annotate(ticket_count=Count("tickets"))

    @admin.display(description="Colour")
    def swatch(self, tag: Tag) -> Any:
        if not tag.colour:
            return "--"
        return format_html(
            '<span style="display:inline-block;width:1rem;height:1rem;'
            'border-radius:3px;background:{}"></span> {}',
            tag.colour,
            tag.colour,
        )

    @admin.display(description="Tickets", ordering="ticket_count")
    def tickets(self, tag: Tag) -> int:
        return getattr(tag, "ticket_count", 0)


@admin.register(CannedReply)
class CannedReplyAdmin(ModelAdmin):
    """Things the desk says often. Inserted into a compose box, never sent by itself."""

    list_display = ("title", "category", "is_active", "used_count")
    list_filter = ("is_active", "category")
    search_fields = ("title", "body")
    autocomplete_fields = ("category",)
    readonly_fields = ("used_count",)
    fieldsets = (
        (None, {"fields": ("title", "body")}),
        (
            "Where it is offered",
            {
                "description": "Leave the category empty to offer it on every ticket.",
                "fields": ("category", "is_active", "used_count"),
            },
        ),
    )


@admin.register(Upload)
class UploadAdmin(ModelAdmin):
    """Files sent but not yet attached to a message. A staging area, so read-only.

    Here because "where did that file go" is a real question and because an
    administrator should be able to see what the staging area is holding before
    setting a retention window on it. Nothing is editable: the row describes a
    file that already exists in storage, and editing the row would only make it
    disagree.
    """

    read_only_admin = True

    list_display = ("name", "owner", "size", "content_type", "claimed", "created_at")
    list_filter = ("content_type", "created_at")
    search_fields = ("name", "owner__username", "owner__email")
    date_hierarchy = "created_at"

    def get_queryset(self, request: HttpRequest) -> QuerySet[Upload]:
        return super().get_queryset(request).select_related("owner", "attachment")

    @admin.display(description="Attached", boolean=True)
    def claimed(self, upload: Upload) -> bool:
        return upload.is_claimed

    def has_add_permission(self, request: HttpRequest) -> bool:
        return False

    def has_change_permission(self, request: HttpRequest, obj: Any = None) -> bool:
        return False


@admin.register(Attachment)
class AttachmentAdmin(ModelAdmin):
    """What was attached to what. A record of the message, so nothing is editable."""

    read_only_admin = True

    list_display = ("name", "ticket", "size", "content_type", "created_at")
    list_filter = ("content_type", "created_at")
    search_fields = ("name", "message__ticket__reference")
    date_hierarchy = "created_at"

    def get_queryset(self, request: HttpRequest) -> QuerySet[Attachment]:
        return super().get_queryset(request).select_related("message__ticket")

    @admin.display(description="Ticket", ordering="message__ticket__reference")
    def ticket(self, attachment: Attachment) -> str:
        return attachment.message.ticket.reference

    def has_add_permission(self, request: HttpRequest) -> bool:
        return False

    def has_change_permission(self, request: HttpRequest, obj: Any = None) -> bool:
        return False


@admin.register(Participant)
class ParticipantAdmin(ModelAdmin):
    """Who is in which conversation, and how far through it. A log, so read-only.

    Registered as well as inlined because the useful question is sometimes the
    other way round -- "what is this person in" rather than "who is in this" --
    and because ``autocomplete_fields`` elsewhere needs a searchable admin.
    """

    read_only_admin = True

    list_display = ("user", "ticket", "role", "joined_at", "last_read_at", "notify")
    list_filter = ("role", "notify", "joined_at")
    search_fields = ("user__username", "user__email", "ticket__reference")
    date_hierarchy = "joined_at"

    def get_queryset(self, request: HttpRequest) -> QuerySet[Participant]:
        return super().get_queryset(request).select_related("user", "ticket")

    def has_add_permission(self, request: HttpRequest) -> bool:
        return False

    def has_change_permission(self, request: HttpRequest, obj: Any = None) -> bool:
        return False


@admin.register(Message)
class MessageAdmin(ModelAdmin):
    """Every message, across every conversation.

    The inline under a ticket is where a thread is read; this is for the search
    that crosses threads -- "who else pasted a card number", "what did we say
    about the outage" -- and for redacting one when the answer is "here".
    """

    list_display = ("created_at", "ticket", "author", "kind", "visibility", "preview")
    list_filter = ("kind", "visibility", "created_at")
    search_fields = ("body", "ticket__reference", "author__username")
    autocomplete_fields = ("ticket", "author")
    date_hierarchy = "created_at"
    readonly_fields = ("id", "created_at", "edited_at", "deleted_at")
    fieldsets = (
        (None, {"fields": ("ticket", "author", "kind", "visibility", "body")}),
        ("Payload", {"classes": ("collapse",), "fields": ("data",)}),
        (
            "Record",
            {
                "classes": ("collapse",),
                "fields": ("id", "created_at", "edited_at", "deleted_at"),
            },
        ),
    )

    def get_queryset(self, request: HttpRequest) -> QuerySet[Message]:
        return super().get_queryset(request).select_related("ticket", "author")

    def has_add_permission(self, request: HttpRequest) -> bool:
        """No -- see :meth:`MessageInline.has_add_permission`."""
        return False

    @admin.display(description="Message")
    def preview(self, message: Message) -> str:
        """The first line, which is what a search result is scanned by."""
        if message.is_deleted:
            return "(deleted)"
        first = message.body.strip().splitlines()[0] if message.body.strip() else ""
        return first[:80] + ("..." if len(first) > 80 else "")


def desk_numbers() -> dict[str, Any]:
    """The numbers the dashboard card shows. Here rather than in the admin UI
    module, so that the app carries its own definition of what matters."""
    live = Ticket.objects.filter(status__in=LIVE_STATUSES)
    return {
        "live": live.count(),
        "unassigned": live.filter(assignee__isnull=True).count(),
        "awaiting": live.filter(first_response_at__isnull=True).count(),
        "breached": sum(1 for ticket in live.select_related("category") if ticket.breached),
    }
