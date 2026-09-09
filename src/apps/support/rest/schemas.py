"""The contract the support endpoints publish.

Three things here are worth pointing at, because all three are per-account and
none of them is a column on the row:

``unread`` is how many messages *this* account has not read in a thread. The
same thread has a different value for the client and for each agent in it, and
both are correct -- see :class:`apps.support.models.Participant`.

``sla.breached`` is computed against the clock every time it is serialised, not
stored. A thread that goes past its deadline while nobody is looking is in
breach the moment somebody looks, with no job having had to run.

The message list a client receives is not the message list an agent receives.
Internal notes are dropped for anybody who is not staff, and the dropping
happens in the queryset rather than in the schema, so there is no shape here
that says "this field is sometimes absent" -- the rows simply are not there.
"""

from datetime import datetime
from typing import Any
from uuid import UUID

from ninja import Schema

from apps.support.models import Kind, MessageKind, Priority, Role, Status, Visibility


class AccountOut(Schema):
    """Who somebody is, as the other side of a conversation may know them.

    Deliberately not the whole account: a client is entitled to know that an
    agent answered and what to call them, and not to their email address.
    """

    id: UUID
    username: str
    staff: bool


class CategoryOut(Schema):
    """What a ticket can be about, and what the desk has promised about it."""

    id: UUID
    name: str
    slug: str
    description: str = ""
    default_priority: Priority
    first_response_minutes: int
    resolution_minutes: int
    """Zero means the desk has made no promise, which is not the same as a
    promise of zero minutes."""


class CategoryRef(Schema):
    id: UUID
    name: str
    slug: str


class TagOut(Schema):
    id: UUID
    name: str
    slug: str
    colour: str = ""


class SlaOut(Schema):
    """The two deadlines, and whether either was missed."""

    first_response_due_at: datetime | None = None
    resolution_due_at: datetime | None = None
    first_response_at: datetime | None = None
    first_response_breached: bool
    resolution_breached: bool
    breached: bool


class AttachmentOut(Schema):
    id: UUID
    name: str
    url: str
    content_type: str = ""
    size: int


class MessageOut(Schema):
    """One message, as this account may read it.

    A deleted message keeps its id and loses its body: two people are reading
    the thread, and a row that simply vanished would leave one of them with a
    message that cannot be marked read, replied to or explained.
    """

    id: UUID
    ticket: UUID
    author: AccountOut | None = None
    """Empty for something the system said, or for a deleted account."""

    kind: MessageKind
    visibility: Visibility
    body: str
    data: dict[str, Any] = {}
    attachments: list[AttachmentOut] = []
    created_at: datetime
    edited_at: datetime | None = None
    deleted: bool


class ParticipantOut(Schema):
    user: AccountOut | None = None
    role: Role
    joined_at: datetime
    last_read_at: datetime | None = None
    notify: bool


class TicketOut(Schema):
    """One thread, without its messages."""

    id: UUID
    reference: str
    """The short code a person quotes down a telephone."""

    kind: Kind
    subject: str
    slug: str = ""
    """A channel's address, so a client can link to `#general` rather than to a
    uuid. Empty for every other kind, which is what has one."""

    status: Status
    priority: Priority
    client: AccountOut | None = None
    assignee: AccountOut | None = None
    category: CategoryRef | None = None
    tags: list[str] = []
    data: dict[str, Any] = {}
    created_at: datetime
    updated_at: datetime | None = None
    last_message_at: datetime | None = None
    resolved_at: datetime | None = None
    closed_at: datetime | None = None
    rating: int | None = None
    rating_comment: str = ""
    sla: SlaOut
    unread: int = 0


class TicketDetailOut(TicketOut):
    """One thread, plus who is in it. Trimmed for a client -- see the service."""

    participants: list[ParticipantOut] = []


class TicketPage(Schema):
    """A page of threads, and how many there were to page through."""

    tickets: list[TicketOut]
    total: int
    """Matching the same filters, ignoring ``limit`` and ``offset``."""

    limit: int
    offset: int


class MessagePage(Schema):
    """A page of one thread, oldest first.

    Oldest first, unlike every other list in this project: a conversation is
    read forwards, and a client that had to reverse each page before rendering
    it would reverse one of them wrongly.
    """

    ticket: UUID
    messages: list[MessageOut]
    total: int
    limit: int
    offset: int


class UnreadOut(Schema):
    """The badge: how much is waiting, and across how many conversations."""

    messages: int
    tickets: int


class OpenIn(Schema):
    """Opening a thread, with the first message already in it.

    A ticket needs a subject and a body -- a thread with nothing in it is not a
    question anybody can answer. A chat needs neither, because a widget opens
    the connection before the visitor has typed anything.
    """

    subject: str = ""
    body: str = ""
    kind: Kind = Kind.TICKET  # type: ignore[assignment]
    category: str | None = None
    """A category slug. Only one that is still open for filing is accepted."""

    priority: Priority | None = None
    upload_ids: list[UUID] = []
    """Files already sent to `/support/uploads`. Claimed by this message."""

    data: dict[str, Any] = {}


class MessageIn(Schema):
    body: str = ""
    upload_ids: list[UUID] = []
    data: dict[str, Any] = {}


class EditIn(Schema):
    body: str


class StatusIn(Schema):
    status: Status


class PriorityIn(Schema):
    priority: Priority


class AssignIn(Schema):
    agent: UUID | None = None
    """Empty puts the thread back in the unassigned queue."""


class TagsIn(Schema):
    tags: list[str] = []
    """The complete set of slugs the thread should now carry. A replacement,
    which is what a tag picker sends."""


class InviteIn(Schema):
    account: UUID
    role: Role = Role.OBSERVER  # type: ignore[assignment]


class RatingIn(Schema):
    score: int
    comment: str = ""


class TypingIn(Schema):
    typing: bool = True


class UploadOut(Schema):
    """A staged file, and the id a message claims it by."""

    id: UUID
    name: str
    url: str
    content_type: str = ""
    size: int


class CannedReplyOut(Schema):
    id: UUID
    title: str
    body: str
    category: str | None = None


class StatsOut(Schema):
    """The numbers a desk runs on."""

    total: int
    open: int
    pending: int
    on_hold: int
    resolved: int
    closed: int
    unassigned: int
    chats: int
    rated: int
    breached: int
    awaiting_first_response: int
    satisfaction: float | None = None
    """The mean rating, or null where nothing has been rated yet."""


class ReadOut(Schema):
    """Confirmation, with the count a client would otherwise refetch for."""

    ticket: UUID
    changed: bool
    """False when it was already in that state. Success either way -- reading a
    read thread is not an error."""

    unread: int
    last_read_at: datetime | None = None


class StatusOut(Schema):
    ticket: UUID
    status: Status
    changed: bool


class PriorityOut(Schema):
    ticket: UUID
    priority: Priority
    changed: bool


class AssignOut(Schema):
    ticket: UUID
    assignee: AccountOut | None = None
    changed: bool


class TagsOut(Schema):
    ticket: UUID
    tags: list[str]
    changed: bool


class RatingOut(Schema):
    ticket: UUID
    rating: int
    comment: str = ""


class TypingOut(Schema):
    ticket: UUID
    typing: bool


# -- rooms ------------------------------------------------------------------


class ChannelOut(TicketOut):
    """A channel as it appears in the directory, joined or not."""

    joined: bool
    members: int


class ChannelIn(Schema):
    name: str
    slug: str = ""
    """Left empty the address is made from the name. Given, it is used as-is."""
    body: str = ""


class GroupIn(Schema):
    name: str
    members: list[UUID] = []
    """Who is in it, named at creation: a group of one is not a group."""
    body: str = ""


class DirectIn(Schema):
    account: UUID


class LeftOut(Schema):
    ticket: UUID
    left: bool
