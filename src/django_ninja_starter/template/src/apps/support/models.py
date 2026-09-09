"""What a conversation between a client and the support desk is made of.

This app is meant to be lifted out and dropped into another Django project, so
it owns everything it needs and asks the project for almost nothing: settings it
defaults for itself, a router mount, and one line of WebSocket routing.

**A ticket is a conversation.** That is the whole design, and it is what lets
live chat and a formal support ticket be one app rather than two that have to be
kept in step. A :class:`Ticket` is a thread; a :class:`Message` is something
somebody said in it. ``kind`` says which of the two ways the thread is being
used -- ``chat`` for a conversation somebody started from a widget and expects
an answer to now, ``ticket`` for one with a subject, a category and a deadline
-- and nothing about the storage differs between them. A chat that turns out to
be a real problem is promoted by setting a subject and a category, not by
copying rows into another table.

**Who may see what is a property of the message, not of the reader.** A message
is ``public`` or ``internal``, and internal means staff-only: the note an agent
leaves for the next agent. Both live in the same thread in the order they were
written, because a thread where the internal notes are somewhere else is a
thread nobody reads in order. Every queryset that a client can reach goes
through :meth:`MessageQuerySet.readable_by`, which drops the internal ones, so
"forgot to filter" is not a mistake a caller can make from outside this module.

**Read state is per participant, not per message.** One row per person per
thread carrying ``last_read_at`` answers "how many did I miss" with a count
rather than with a join against every message anybody has ever sent, and it is
the only shape that stays cheap when a busy thread has a thousand messages and
four people in it. The cost is that read state has one-message granularity in
time rather than in identity, which is what a conversation actually wants: you
read up to a point, not a scattered subset.

**The SLA is stored as deadlines, never as a breach flag.** A flag would need
something to set it -- a cron job, a signal, a background worker -- and would be
wrong for the whole window between the deadline passing and that thing running.
Two datetimes are written when the ticket is created and compared against the
clock when anybody asks, so a breach is true the instant it is true and no
process has to be running for it to be.

**Events are messages.** A status change, an assignment, a close is written into
the thread as a ``event`` message rather than into a separate audit table, so
the history a client is shown and the history the desk audits are one list that
cannot disagree. They are readable by whoever the change concerned: a client
should see "an agent resolved this", and should not see who it was reassigned
between.
"""

import secrets
import uuid
from collections.abc import Iterable
from datetime import UTC, datetime
from typing import Any

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import IntegrityError, models, transaction
from django.db.models import Count, F, Max, OuterRef, Q, Subquery, Value
from django.db.models.functions import Coalesce
from django.utils import timezone
from django.utils.text import slugify

#: The "before everything" moment an absent read watermark stands in for. A
#: datetime rather than ``None``, because SQL compares nothing to NULL
#: successfully -- see :meth:`TicketQuerySet.with_unread`.
EPOCH = datetime(1970, 1, 1, tzinfo=UTC)

#: How many hex characters a ticket reference carries. Six is 16 million values,
#: which is enough that the collision retry below effectively never runs, and
#: short enough that somebody can read one down a telephone.
REFERENCE_ENTROPY = 6


class Kind(models.TextChoices):
    """Which of the two ways a thread is being used."""

    CHAT = "chat", "Live chat"
    TICKET = "ticket", "Support ticket"


class Status(models.TextChoices):
    """Where a thread has got to.

    ``pending`` is the one worth explaining: it means the desk has answered and
    is waiting on the client. Without it, a queue sorted by age cannot tell a
    ticket nobody has looked at from one that has been answered twice and is
    waiting for a reply, and the desk ends up chasing its own messages.
    """

    OPEN = "open", "Open"
    PENDING = "pending", "Waiting on the client"
    ON_HOLD = "on_hold", "On hold"
    RESOLVED = "resolved", "Resolved"
    CLOSED = "closed", "Closed"


#: The statuses that still want somebody's attention. Named rather than written
#: out at each call site, because "which of these counts as open" is a question
#: several modules ask and all of them have to agree on the answer.
LIVE_STATUSES = (Status.OPEN, Status.PENDING, Status.ON_HOLD)

#: A thread that has been settled. Resolved is separate from closed so that a
#: client can disagree -- see :meth:`Ticket.reopen`.
SETTLED_STATUSES = (Status.RESOLVED, Status.CLOSED)


class Priority(models.TextChoices):
    """How far up the queue this belongs. Advisory: nothing here reorders on it."""

    LOW = "low", "Low"
    NORMAL = "normal", "Normal"
    HIGH = "high", "High"
    URGENT = "urgent", "Urgent"


class Role(models.TextChoices):
    """Why somebody is in a thread."""

    CLIENT = "client", "Client"
    AGENT = "agent", "Agent"
    OBSERVER = "observer", "Observer"


class MessageKind(models.TextChoices):
    """What kind of thing was said."""

    REPLY = "reply", "Reply"
    NOTE = "note", "Internal note"
    EVENT = "event", "Event"


class Visibility(models.TextChoices):
    """Who may read one message."""

    PUBLIC = "public", "Everybody in the thread"
    INTERNAL = "internal", "Staff only"


class Category(models.Model):
    """What a ticket is about, and what the desk has promised about it.

    The SLA windows live here rather than on the ticket because that is where
    the promise is actually made: "billing questions are answered within an
    hour" is a statement about billing, and putting the number on every ticket
    would mean changing the promise changed nothing about the tickets already
    open under it. A ticket copies the windows it was created under into its own
    deadlines, so the promise it was made is the promise it is held to even
    after the category is edited.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=120, unique=True)
    slug = models.SlugField(max_length=140, unique=True, blank=True)
    description = models.TextField(blank=True)
    default_priority = models.CharField(
        max_length=16,
        choices=Priority.choices,
        default=Priority.NORMAL,
        help_text="What a ticket in this category starts at unless the client says otherwise.",
    )
    first_response_minutes = models.PositiveIntegerField(
        default=0,
        help_text="How long the desk has to say something. Zero means no promise.",
    )
    resolution_minutes = models.PositiveIntegerField(
        default=0,
        help_text="How long the desk has to settle it. Zero means no promise.",
    )
    is_active = models.BooleanField(
        default=True,
        help_text="Unset to stop offering it, without touching the tickets already filed.",
    )
    order = models.PositiveIntegerField(default=0, help_text="Where it sits in a picker.")

    class Meta:
        ordering = ["order", "name"]
        verbose_name_plural = "categories"

    def __str__(self) -> str:
        return self.name

    def save(self, *args: Any, **kwargs: Any) -> None:
        if not self.slug:
            self.slug = slugify(self.name)[:140]
        super().save(*args, **kwargs)


class Tag(models.Model):
    """A label the desk puts on a thread for its own purposes.

    Separate from the category because a thread has exactly one of those and any
    number of these, and because a category is offered to the client while a tag
    is the desk's own vocabulary -- ``needs-translation``, ``escalated-twice``.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=60, unique=True)
    slug = models.SlugField(max_length=80, unique=True, blank=True)
    colour = models.CharField(
        max_length=20, blank=True, help_text="Any CSS colour. Advisory; used by the admin."
    )

    class Meta:
        ordering = ["name"]

    def __str__(self) -> str:
        return self.name

    def save(self, *args: Any, **kwargs: Any) -> None:
        if not self.slug:
            self.slug = slugify(self.name)[:80]
        super().save(*args, **kwargs)


class TicketQuerySet(models.QuerySet["Ticket"]):
    """Every way a ticket list is narrowed. No method here widens what a caller sees."""

    def for_client(self, user: Any) -> "TicketQuerySet":
        """Only the threads this account opened. The whole of a client's world."""
        return self.filter(client=user)

    def visible_to(self, user: Any) -> "TicketQuerySet":
        """What one account is entitled to see at all.

        Staff see the desk; everybody else sees the threads they opened and the
        ones they were added to as an observer. This is the only place the
        distinction is made, so an endpoint cannot accidentally serve a client
        the queue.
        """
        if getattr(user, "is_staff", False):
            return self.all()
        return self.filter(Q(client=user) | Q(participants__user=user)).distinct()

    def live(self) -> "TicketQuerySet":
        """Still wanting somebody's attention."""
        return self.filter(status__in=LIVE_STATUSES)

    def settled(self) -> "TicketQuerySet":
        return self.filter(status__in=SETTLED_STATUSES)

    def assigned_to(self, user: Any) -> "TicketQuerySet":
        return self.filter(assignee=user)

    def unassigned(self) -> "TicketQuerySet":
        return self.filter(assignee__isnull=True)

    def search(self, term: str) -> "TicketQuerySet":
        """Match a term against the reference, the subject and what was said in it.

        Deliberately ``icontains`` rather than a full-text index: this project
        runs on SQLite in development and on PostgreSQL in production, and a
        search that only worked on one of them would be a feature that behaves
        differently in the place it is written from the place it runs. A desk
        large enough to need a real index has outgrown the starter's opinion and
        can add ``SearchVector`` here without any caller changing.

        Only public messages are searched for a client -- see
        :meth:`for_search_by`, which is what callers should reach for.
        """
        term = term.strip()
        if not term:
            return self
        return self.filter(
            Q(reference__icontains=term)
            | Q(subject__icontains=term)
            | Q(messages__body__icontains=term)
        ).distinct()

    def for_search_by(self, user: Any, term: str) -> "TicketQuerySet":
        """Search, without letting a client match on an internal note.

        Searching the message body is what makes search useful and is also the
        one way a client could learn that a staff-only note exists: a term that
        appears nowhere they can read would still return the thread. So for a
        client the message half of the search is restricted to public messages.
        """
        term = term.strip()
        if not term:
            return self
        if getattr(user, "is_staff", False):
            return self.search(term)
        return self.filter(
            Q(reference__icontains=term)
            | Q(subject__icontains=term)
            | Q(
                messages__body__icontains=term,
                messages__visibility=Visibility.PUBLIC,
                messages__deleted_at__isnull=True,
            )
        ).distinct()

    def with_activity(self) -> "TicketQuerySet":
        """Annotate ``message_count`` and ``last_activity_at`` in the list query.

        A queue that renders "12 messages, last one an hour ago" per row would
        otherwise cost two queries a row, and a queue is the one screen where
        that is measured in hundreds.
        """
        return self.annotate(
            message_count=Count("messages", filter=Q(messages__deleted_at__isnull=True)),
            last_activity_at=Max("messages__created_at"),
        )

    def with_unread(self, user: Any) -> "TicketQuerySet":
        """Annotate how many messages this account has not read, per thread.

        A correlated count rather than a join: a join against the messages would
        multiply the ticket rows by them, and a queue of fifty threads would
        come back as a few thousand rows to be collapsed in Python.

        The subquery reads this account's watermark and counts what came after
        it, treating a missing watermark -- an account that has read nothing, or
        is not a participant at all -- as counting everything. That is the
        ``Coalesce`` onto the epoch: SQL comparisons against NULL are NULL, so
        without it every unread count for a fresh participant would come back
        zero, which is the one wrong answer that looks right.

        Your own messages are excluded, and for a client so are the internal
        notes, so the number annotated here is the number the badge should show.
        """
        watermark = Participant.objects.filter(ticket=OuterRef("pk"), user=user).values(
            "last_read_at"
        )[:1]
        # The same watermark, one level deeper. Inside the message subquery a
        # plain ``OuterRef("pk")`` resolves against *messages* rather than
        # tickets, matches no participant, and so reads as "has read nothing" --
        # a badge that never clears no matter how much you read.
        inner_watermark = Participant.objects.filter(
            ticket=OuterRef(OuterRef("pk")), user=user
        ).values("last_read_at")[:1]
        unread = (
            Message.objects.filter(ticket=OuterRef("pk"), deleted_at__isnull=True)
            .exclude(author=user)
            .filter(created_at__gt=Coalesce(Subquery(inner_watermark), Value(EPOCH)))
        )
        if not getattr(user, "is_staff", False):
            unread = unread.filter(visibility=Visibility.PUBLIC)
        return self.annotate(
            last_read_at=Subquery(watermark),
            unread=Coalesce(
                Subquery(
                    unread.order_by()
                    .values("ticket")
                    .annotate(total=Count("id"))
                    .values("total")[:1],
                    output_field=models.IntegerField(),
                ),
                Value(0),
            ),
        )

    def ordered_for_queue(self) -> "TicketQuerySet":
        """Newest activity first, falling back to when it was opened.

        ``last_message_at`` is denormalised onto the row precisely so that this
        ordering is an index scan rather than an aggregate over every message in
        the table.
        """
        return self.order_by(F("last_message_at").desc(nulls_last=True), "-created_at")


class Ticket(models.Model):
    """One conversation between a client and the desk."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    reference = models.CharField(
        max_length=32,
        unique=True,
        editable=False,
        help_text="The short code a person quotes. Generated; never reused.",
    )
    kind = models.CharField(max_length=16, choices=Kind.choices, default=Kind.TICKET)
    client = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="support_tickets",
        help_text="Whose problem this is. The one account that always sees the thread.",
    )
    subject = models.CharField(
        max_length=200,
        blank=True,
        help_text=(
            "Empty is allowed: a live chat often has no subject until it turns out to need one."
        ),
    )
    category = models.ForeignKey(
        Category,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="tickets",
        help_text="Kept as a null on delete: retiring a category must not delete its history.",
    )
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.OPEN)
    priority = models.CharField(max_length=16, choices=Priority.choices, default=Priority.NORMAL)
    assignee = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="support_assignments",
        help_text="The agent who owns it. Empty means it is still in the unassigned queue.",
    )
    tags = models.ManyToManyField(Tag, blank=True, related_name="tickets")
    data = models.JSONField(
        default=dict,
        blank=True,
        help_text="Whatever the client that opened this wants carried with it. Sent verbatim.",
    )

    created_at = models.DateTimeField(default=timezone.now, editable=False)
    updated_at = models.DateTimeField(auto_now=True)
    last_message_at = models.DateTimeField(
        null=True,
        blank=True,
        editable=False,
        help_text="Denormalised so a queue can be ordered by an index rather than an aggregate.",
    )
    first_response_at = models.DateTimeField(
        null=True,
        blank=True,
        editable=False,
        help_text="When a member of staff first said something. The SLA clock stops here.",
    )
    resolved_at = models.DateTimeField(null=True, blank=True, editable=False)
    closed_at = models.DateTimeField(null=True, blank=True, editable=False)

    first_response_due_at = models.DateTimeField(
        null=True,
        blank=True,
        editable=False,
        help_text="Copied from the category when the ticket was opened. Empty means no promise.",
    )
    resolution_due_at = models.DateTimeField(null=True, blank=True, editable=False)

    rating = models.PositiveSmallIntegerField(
        null=True,
        blank=True,
        help_text="One to five, from the client, once it is settled.",
    )
    rating_comment = models.TextField(blank=True)
    rated_at = models.DateTimeField(null=True, blank=True, editable=False)

    objects = TicketQuerySet.as_manager()

    class Meta:
        ordering = ["-created_at", "-id"]
        indexes = [
            models.Index(fields=["client", "-created_at"]),
            models.Index(fields=["status", "-last_message_at"]),
            models.Index(fields=["assignee", "status"]),
            models.Index(fields=["kind", "status"]),
            # Pruning walks settled threads by age alone.
            models.Index(fields=["closed_at"]),
        ]
        constraints = [
            models.CheckConstraint(
                condition=Q(rating__isnull=True) | Q(rating__gte=1, rating__lte=5),
                name="support_rating_between_one_and_five",
                violation_error_message="A rating is one to five stars.",
            )
        ]

    def __str__(self) -> str:
        return f"{self.reference} {self.subject or self.get_kind_display()}"

    # -- lifecycle --------------------------------------------------------

    def save(self, *args: Any, **kwargs: Any) -> None:
        """Give a new ticket a reference and its deadlines before it is written.

        Both are done here rather than in a service, because a ticket created in
        the admin, in a shell or by a management command should be as complete
        as one created through the API -- and a reference is not something a row
        can be missing and still be quoted down a telephone.
        """
        if not self.reference:
            self.reference = self._next_reference()
        if self._state.adding:
            self._apply_sla()
        super().save(*args, **kwargs)

    def _next_reference(self) -> str:
        """A short, unique, unguessable code.

        Random rather than sequential. A sequence would need a counter row and a
        lock to be safe under concurrency, and -- worse -- would publish how
        many tickets the desk has ever had to anybody who opened one. The
        collision retry is in :func:`create_ticket`, which is the only place a
        reference is written under a race.
        """
        return f"{reference_prefix()}-{secrets.token_hex(REFERENCE_ENTROPY // 2).upper()}"

    def _apply_sla(self) -> None:
        """Copy the promise this ticket was opened under onto the ticket itself.

        A category edited next month must not move the deadline of a ticket
        opened today: the promise made is the promise kept. A ticket with no
        category, or a category promising nothing, gets no deadlines -- which is
        not the same as a deadline of zero, and is why both columns are nullable
        rather than defaulted.
        """
        if self.category_id is None:
            return
        category = self.category
        opened = self.created_at or timezone.now()
        if category.first_response_minutes:
            self.first_response_due_at = opened + timezone.timedelta(
                minutes=category.first_response_minutes
            )
        if category.resolution_minutes:
            self.resolution_due_at = opened + timezone.timedelta(
                minutes=category.resolution_minutes
            )

    def clean(self) -> None:
        """Say the two things the admin should hear as a form error rather than a traceback."""
        if self.rating is not None and self.status not in SETTLED_STATUSES:
            raise ValidationError(
                {"rating": "A ticket is rated once it is settled, not while it is still open."}
            )
        if self.assignee_id is not None and not getattr(self.assignee, "is_staff", False):
            raise ValidationError({"assignee": "Only a member of staff can own a ticket."})

    # -- what the row means ----------------------------------------------

    @property
    def is_open(self) -> bool:
        return self.status in LIVE_STATUSES

    @property
    def is_settled(self) -> bool:
        return self.status in SETTLED_STATUSES

    @property
    def awaiting_first_response(self) -> bool:
        return self.first_response_at is None

    @property
    def first_response_breached(self) -> bool:
        """Whether the desk missed the first-response promise.

        Compared against the clock now, not against a flag somebody had to set:
        a breach is true the instant it is true. Once the desk has answered, the
        answer's own timestamp is what is compared, so a breach stays a breach
        rather than becoming worse forever.
        """
        if self.first_response_due_at is None:
            return False
        reference_time = self.first_response_at or timezone.now()
        return reference_time > self.first_response_due_at

    @property
    def resolution_breached(self) -> bool:
        if self.resolution_due_at is None:
            return False
        reference_time = self.resolved_at or self.closed_at or timezone.now()
        return reference_time > self.resolution_due_at

    @property
    def breached(self) -> bool:
        return self.first_response_breached or self.resolution_breached

    def participant_for(self, user: Any) -> "Participant | None":
        return self.participants.filter(user=user).first()

    def readable_by(self, user: Any) -> bool:
        """Whether one account may see this thread at all."""
        if getattr(user, "is_staff", False):
            return True
        if self.client_id == getattr(user, "pk", None):
            return True
        return self.participants.filter(user=user).exists()

    def staff_ids(self) -> list[Any]:
        """The accounts on the desk side of this thread, for addressing a notification."""
        return list(
            self.participants.filter(role__in=(Role.AGENT, Role.OBSERVER)).values_list(
                "user_id", flat=True
            )
        )


def reference_prefix() -> str:
    """The letters in front of a ticket reference. A setting, because it is branding."""
    return str(getattr(settings, "SUPPORT_REFERENCE_PREFIX", "SUP")).upper()


class Participant(models.Model):
    """One account's membership of one thread, and how far through it they are.

    Created for the client when the thread is opened and for an agent the first
    time they say something in it or are assigned to it, so the row's existence
    means "this person is in this conversation" and never "this person has read
    something".

    ``last_read_at`` is a watermark rather than a set of message ids. See the
    module docstring: a conversation is read up to a point.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    ticket = models.ForeignKey(Ticket, on_delete=models.CASCADE, related_name="participants")
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="support_participations"
    )
    role = models.CharField(max_length=16, choices=Role.choices, default=Role.CLIENT)
    joined_at = models.DateTimeField(default=timezone.now, editable=False)
    last_read_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text=(
            "Everything in this thread older than this has been read. Empty means none of it."
        ),
    )
    notify = models.BooleanField(
        default=True,
        help_text="Unset to stay in the thread without being told about every message.",
    )

    class Meta:
        ordering = ["joined_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["ticket", "user"], name="support_one_participation_per_account"
            )
        ]
        indexes = [models.Index(fields=["user", "-joined_at"])]

    def __str__(self) -> str:
        return f"{self.user} in {self.ticket_id} ({self.role})"


class MessageQuerySet(models.QuerySet["Message"]):
    def alive(self) -> "MessageQuerySet":
        """Everything not deleted. A deletion is a tombstone -- see :class:`Message`."""
        return self.filter(deleted_at__isnull=True)

    def public(self) -> "MessageQuerySet":
        return self.filter(visibility=Visibility.PUBLIC)

    def readable_by(self, user: Any) -> "MessageQuerySet":
        """What one account may read: everything for staff, the public half otherwise.

        The single chokepoint for the app's one real confidentiality rule. Every
        path a client can reach goes through here, so an endpoint that forgets
        to filter does not exist -- there is nothing else to call.
        """
        if getattr(user, "is_staff", False):
            return self
        return self.public()

    def unread_for(self, user: Any, since: Any) -> "MessageQuerySet":
        """What this account has not read: newer than its watermark, and not its own.

        Your own messages are never unread. A client that sent three messages
        and got no reply should see a badge of zero, not of three.
        """
        messages = self.alive().readable_by(user).exclude(author=user)
        return messages if since is None else messages.filter(created_at__gt=since)


class Message(models.Model):
    """Something said in a thread -- by a person, or by the system on their behalf.

    A deletion is a tombstone rather than a delete. Two people are reading the
    same thread over two sockets, and a row that vanishes leaves one of them
    with a message that cannot be marked read, cannot be replied to and cannot
    be explained. So ``deleted_at`` is set, the body is kept for the audit the
    desk will eventually want, and every reader is told the message is gone.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    ticket = models.ForeignKey(Ticket, on_delete=models.CASCADE, related_name="messages")
    author = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="support_messages",
        help_text="Empty for something the system said, or for a deleted account.",
    )
    kind = models.CharField(max_length=16, choices=MessageKind.choices, default=MessageKind.REPLY)
    visibility = models.CharField(
        max_length=16, choices=Visibility.choices, default=Visibility.PUBLIC
    )
    body = models.TextField(blank=True)
    data = models.JSONField(
        default=dict,
        blank=True,
        help_text="For an event, what changed. For a reply, whatever the client attached.",
    )
    created_at = models.DateTimeField(default=timezone.now, editable=False)
    edited_at = models.DateTimeField(null=True, blank=True, editable=False)
    deleted_at = models.DateTimeField(null=True, blank=True, editable=False)

    objects = MessageQuerySet.as_manager()

    class Meta:
        ordering = ["created_at", "id"]
        indexes = [
            models.Index(fields=["ticket", "created_at"]),
            models.Index(fields=["author", "-created_at"]),
        ]
        constraints = [
            models.CheckConstraint(
                condition=~Q(kind=MessageKind.NOTE) | Q(visibility=Visibility.INTERNAL),
                name="support_note_is_internal",
                violation_error_message="An internal note cannot be public.",
            )
        ]

    def __str__(self) -> str:
        return f"{self.get_kind_display()} in {self.ticket_id}"

    @property
    def is_deleted(self) -> bool:
        return self.deleted_at is not None

    @property
    def is_internal(self) -> bool:
        return self.visibility == Visibility.INTERNAL

    @property
    def is_system(self) -> bool:
        return self.kind == MessageKind.EVENT

    def clean(self) -> None:
        if self.kind == MessageKind.NOTE and self.visibility != Visibility.INTERNAL:
            raise ValidationError({"visibility": "An internal note cannot be public."})

    def editable_by(self, user: Any) -> bool:
        """Only the author, only a reply, and never once it is deleted.

        Staff are not given an exception. Editing what somebody else is recorded
        as having said is not moderation, it is forgery, and a desk that needs a
        message gone has :meth:`delete`, which leaves a tombstone saying so.
        """
        if self.is_deleted or self.kind == MessageKind.EVENT:
            return False
        return self.author_id is not None and self.author_id == getattr(user, "pk", None)

    def deletable_by(self, user: Any) -> bool:
        """The author, or staff. Staff may retract what a client posted by mistake."""
        if self.is_deleted or self.kind == MessageKind.EVENT:
            return False
        if getattr(user, "is_staff", False):
            return True
        return self.author_id is not None and self.author_id == getattr(user, "pk", None)


class Upload(models.Model):
    """A file somebody has sent but not yet attached to anything.

    Attachments are two steps because the two transports that matter cannot both
    do it in one. HTTP can carry a multipart body; a WebSocket frame is JSON and
    cannot. So a file is uploaded once over HTTP, which answers with an id, and
    the message that carries it is then sent over whichever transport the client
    is already using -- naming the id. A client on the socket therefore attaches
    files without opening a second connection, and the socket does not have to
    learn to parse multipart.

    An upload is owned by whoever sent it and may be claimed exactly once: the
    unique constraint on the attachment is what makes "name somebody else's
    upload id" a refusal rather than a way to read their file.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="support_uploads"
    )
    name = models.CharField(max_length=255, help_text="The file's own name, as sent.")
    url = models.CharField(max_length=1000, help_text="Where the configured storage serves it.")
    content_type = models.CharField(max_length=120, blank=True)
    size = models.PositiveBigIntegerField(default=0)
    created_at = models.DateTimeField(default=timezone.now, editable=False)

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["owner", "-created_at"])]

    def __str__(self) -> str:
        return self.name

    @property
    def is_claimed(self) -> bool:
        return hasattr(self, "attachment")


class Attachment(models.Model):
    """One file, attached to one message.

    The columns are copied off the :class:`Upload` rather than read through it,
    because they are what the message said at the time: a file's stored name is
    part of the record, and the upload row is a staging area that a retention
    policy is entitled to sweep.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    message = models.ForeignKey(Message, on_delete=models.CASCADE, related_name="attachments")
    upload = models.OneToOneField(
        Upload,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="attachment",
        help_text="What was claimed to create this. Kept so an upload cannot be claimed twice.",
    )
    name = models.CharField(max_length=255)
    url = models.CharField(max_length=1000)
    content_type = models.CharField(max_length=120, blank=True)
    size = models.PositiveBigIntegerField(default=0)
    created_at = models.DateTimeField(default=timezone.now, editable=False)

    class Meta:
        ordering = ["created_at"]

    def __str__(self) -> str:
        return self.name


class CannedReply(models.Model):
    """Something the desk says often enough to have written down once.

    Staff-only, and never applied automatically: it is inserted into the compose
    box, which is the difference between a shortcut and a robot answering on
    somebody's behalf.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    title = models.CharField(max_length=120, unique=True)
    body = models.TextField()
    category = models.ForeignKey(
        Category,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="canned_replies",
        help_text="Offer it only on tickets in this category. Empty offers it everywhere.",
    )
    is_active = models.BooleanField(default=True)
    used_count = models.PositiveIntegerField(
        default=0,
        editable=False,
        help_text="How often it has been fetched. Tells the desk what to keep.",
    )

    class Meta:
        ordering = ["title"]
        verbose_name_plural = "canned replies"

    def __str__(self) -> str:
        return self.title


# -- the operations everything else is built out of -----------------------
#
# Written as functions on the module rather than methods on the model, for the
# same reason the notifications app does it: they are the vocabulary the service
# layer, the admin, a management command and a shell session all reach for, and
# several of them touch more than one row.


def create_ticket(
    client: Any,
    *,
    kind: str = str(Kind.TICKET),
    subject: str = "",
    category: Category | None = None,
    priority: str = "",
    data: dict[str, Any] | None = None,
) -> Ticket:
    """Open a thread, and put its client in it.

    The reference is retried on collision rather than assumed unique. Six hex
    characters make that effectively never, but "effectively never" happens to
    somebody, and the alternative is a 500 on the one call a person makes when
    something has already gone wrong for them.
    """
    for attempt in range(5):
        try:
            with transaction.atomic():
                ticket = Ticket.objects.create(
                    client=client,
                    kind=kind,
                    subject=subject.strip(),
                    category=category,
                    priority=priority
                    or (category.default_priority if category else Priority.NORMAL),
                    data=data or {},
                )
                Participant.objects.create(ticket=ticket, user=client, role=Role.CLIENT)
                return ticket
        except IntegrityError:
            if attempt == 4:  # pragma: no cover - a five-in-a-row reference collision
                raise
    raise AssertionError("unreachable")  # pragma: no cover


def join(ticket: Ticket, user: Any, role: str = str(Role.AGENT)) -> Participant:
    """Put somebody in a thread, or return the membership they already had.

    Idempotent, because every path that could add an agent -- assigning, replying,
    leaving a note -- calls it, and none of them should care whether one of the
    others got there first.
    """
    participant, _ = Participant.objects.get_or_create(
        ticket=ticket, user=user, defaults={"role": role}
    )
    return participant


def post_message(
    ticket: Ticket,
    author: Any | None,
    body: str,
    *,
    kind: str = str(MessageKind.REPLY),
    visibility: str = "",
    data: dict[str, Any] | None = None,
    uploads: Iterable[Upload] = (),
) -> Message:
    """Say something in a thread, and move everything that a message moves.

    One function, because a message is never only a row. It stops the
    first-response clock if it is the desk's first word, it moves the thread's
    ``last_message_at`` so the queue ordering stays an index scan, it flips a
    resolved thread back to open if the client has more to say, and it claims
    whichever uploads were named. Splitting those across the callers is how one
    of them ends up not doing the third.

    A note defaults to internal and cannot be anything else -- the check
    constraint says so, and this saves a caller from having to know.
    """
    if kind == MessageKind.NOTE:
        visibility = str(Visibility.INTERNAL)
    with transaction.atomic():
        message = Message.objects.create(
            ticket=ticket,
            author=author,
            kind=kind,
            visibility=visibility or Visibility.PUBLIC,
            body=body,
            data=data or {},
        )
        _attach(message, uploads)
        _advance_ticket(ticket, message, author)
    return message


def _attach(message: Message, uploads: Iterable[Upload]) -> None:
    """Turn staged uploads into attachments on a message, copying what they said."""
    Attachment.objects.bulk_create(
        [
            Attachment(
                message=message,
                upload=upload,
                name=upload.name,
                url=upload.url,
                content_type=upload.content_type,
                size=upload.size,
            )
            for upload in uploads
        ]
    )


def _advance_ticket(ticket: Ticket, message: Message, author: Any | None) -> None:
    """Everything a new message changes about the thread it landed in.

    A public reply from staff is the one that stops the first-response clock: an
    internal note is not an answer to the client, and an event the system wrote
    is not somebody responding. A public reply from the client reopens a thread
    the desk had resolved, because "resolved" is the desk's opinion and the
    client is entitled to disagree by carrying on talking. A closed thread is
    not reopened that way -- closing is final, and reopening it is a request the
    client makes explicitly.
    """
    fields = ["last_message_at", "updated_at"]
    ticket.last_message_at = message.created_at

    from_staff = author is not None and getattr(author, "is_staff", False)
    substantive = message.kind == MessageKind.REPLY and message.visibility == Visibility.PUBLIC

    if from_staff and substantive and ticket.first_response_at is None:
        ticket.first_response_at = message.created_at
        fields.append("first_response_at")
    if from_staff and substantive and ticket.status == Status.OPEN:
        ticket.status = Status.PENDING
        fields.append("status")
    if not from_staff and substantive and ticket.status in (Status.PENDING, Status.RESOLVED):
        ticket.status = Status.OPEN
        ticket.resolved_at = None
        fields.extend(["status", "resolved_at"])

    ticket.save(update_fields=sorted(set(fields)))


def edit_message(message: Message, body: str) -> Message:
    """Rewrite a message, and record that it was rewritten.

    ``edited_at`` is not a nicety: a thread where a message can change without
    saying so is a thread neither side can rely on having read.
    """
    message.body = body
    message.edited_at = timezone.now()
    message.save(update_fields=["body", "edited_at"])
    return message


def delete_message(message: Message) -> Message:
    """Retract a message, leaving a tombstone. Never a row delete -- see :class:`Message`."""
    message.deleted_at = timezone.now()
    message.save(update_fields=["deleted_at"])
    return message


def mark_read(ticket: Ticket, user: Any, at: Any = None) -> bool:
    """Move this account's watermark forward. Returns whether it moved.

    Two things it refuses to do, and both matter to a client that fires this
    more often than it needs to -- which every client does, because "mark read"
    is what a scroll handler calls.

    **It never moves backwards.** Two sockets belonging to the same person both
    reporting what they have read would otherwise let the older one un-read the
    newer's progress, and a badge that goes *up* when you read something is
    worse than no badge.

    **It does not move when there was nothing unread.** Otherwise the watermark
    would advance to the current instant on every call and every call would
    report a change, so a caller could never tell "you have caught up" from
    "there was nothing to catch up on" -- and the row would be written on every
    scroll for the rest of the conversation.
    """
    participant = join(ticket, user, role=_role_for(ticket, user))
    moment = at or timezone.now()
    if participant.last_read_at is not None and participant.last_read_at >= moment:
        return False
    if unread_count(ticket, user) == 0:
        return False
    participant.last_read_at = moment
    participant.save(update_fields=["last_read_at"])
    return True


def mark_unread(ticket: Ticket, user: Any) -> bool:
    """Drop the watermark entirely, putting the whole thread back in the badge."""
    participant = ticket.participant_for(user)
    if participant is None or participant.last_read_at is None:
        return False
    participant.last_read_at = None
    participant.save(update_fields=["last_read_at"])
    return True


def _role_for(ticket: Ticket, user: Any) -> str:
    """What somebody joining this thread is: its client, or somebody from the desk."""
    if ticket.client_id == getattr(user, "pk", None):
        return str(Role.CLIENT)
    return str(Role.AGENT) if getattr(user, "is_staff", False) else str(Role.OBSERVER)


def unread_count(ticket: Ticket, user: Any) -> int:
    """How many messages in this thread this account has not read."""
    participant = ticket.participant_for(user)
    since = participant.last_read_at if participant else None
    return Message.objects.filter(ticket=ticket).unread_for(user, since).count()


def total_unread(user: Any) -> int:
    """The badge number across every thread this account can see.

    A loop rather than one aggregate, because the watermark comparison is per
    thread and expressing it as a single query means a correlated subquery whose
    plan is worse than the loop on every database this project supports. A
    person is in tens of threads, not tens of thousands; if that stops being
    true, this is the function to rewrite and the only one.
    """
    total = 0
    tickets = Ticket.objects.visible_to(user).prefetch_related("participants")
    for ticket in tickets:
        total += unread_count(ticket, user)
    return total


def set_status(ticket: Ticket, status: str, *, by: Any | None = None) -> bool:
    """Move a thread to a status, stamping whichever timestamp that status means.

    Returns whether anything changed, so "resolve this" fired twice reports
    honestly rather than writing a second event into the thread.
    """
    if ticket.status == status:
        return False
    now = timezone.now()
    fields = ["status", "updated_at"]
    ticket.status = status
    if status == Status.RESOLVED:
        ticket.resolved_at = now
        fields.append("resolved_at")
    if status == Status.CLOSED:
        ticket.closed_at = now
        ticket.resolved_at = ticket.resolved_at or now
        fields.extend(["closed_at", "resolved_at"])
    if status in LIVE_STATUSES:
        ticket.resolved_at = None
        ticket.closed_at = None
        fields.extend(["resolved_at", "closed_at"])
    ticket.save(update_fields=sorted(set(fields)))
    return True


def assign(ticket: Ticket, agent: Any | None) -> bool:
    """Give a thread to an agent, or put it back in the unassigned queue."""
    if ticket.assignee_id == getattr(agent, "pk", None):
        return False
    ticket.assignee = agent
    ticket.save(update_fields=["assignee", "updated_at"])
    if agent is not None:
        join(ticket, agent, role=str(Role.AGENT))
    return True


def set_priority(ticket: Ticket, priority: str) -> bool:
    if ticket.priority == priority:
        return False
    ticket.priority = priority
    ticket.save(update_fields=["priority", "updated_at"])
    return True


def rate(ticket: Ticket, score: int, comment: str = "") -> None:
    """Record what the client thought. Overwrites, because an opinion may change."""
    ticket.rating = score
    ticket.rating_comment = comment
    ticket.rated_at = timezone.now()
    ticket.save(update_fields=["rating", "rating_comment", "rated_at", "updated_at"])


def prune(older_than: Any) -> int:
    """Delete threads closed before ``older_than``. Returns how many went.

    Only closed ones. An open thread is somebody's unanswered question however
    old it is, and a retention policy that deletes those is a retention policy
    that loses complaints.
    """
    deleted, _ = Ticket.objects.filter(status=Status.CLOSED, closed_at__lt=older_than).delete()
    return deleted


def prune_uploads(older_than: Any) -> int:
    """Delete staged uploads nobody ever attached. Returns how many went.

    Claimed ones are left alone whatever their age: the attachment points at
    them to keep the claim unique, and deleting the row would let the same file
    be claimed a second time.
    """
    deleted, _ = Upload.objects.filter(created_at__lt=older_than, attachment__isnull=True).delete()
    return deleted
