"""GraphQL types for a support conversation.

The types mirror the REST schemas one for one, and are built from the same
service payloads, so a field that exists on one transport exists on the other
with the same name and the same meaning.

``unread`` and ``sla.breached`` are the fields worth pointing at, for the same
reasons the REST schemas give: the first is per account rather than a column,
and the second is computed against the clock every time it is asked for.

**Every type here is named ``Support…`` in the schema**, while the Python class
keeps the plain name. One GraphQL document is assembled from every installed
app -- see :mod:`config.graph` -- so a bare ``Category`` would collide with the
shop's the moment both apps are enabled, and Strawberry rightly refuses to
build a schema with two types of one name. The Python names stay short because
inside this package there is nothing to collide with.
"""

from typing import Any

import strawberry
from strawberry.scalars import JSON


@strawberry.type(name="SupportAccount")
class AccountType:
    """Who somebody is, as the other side of a conversation may know them."""

    id: str
    username: str
    staff: bool


@strawberry.type(name="SupportCategoryRef")
class CategoryRefType:
    id: str
    name: str
    slug: str


@strawberry.type(name="SupportCategory")
class CategoryType:
    """What a ticket can be about, and what the desk promised about it."""

    id: str
    name: str
    slug: str
    description: str
    default_priority: str
    first_response_minutes: int
    resolution_minutes: int


@strawberry.type(name="SupportTag")
class TagType:
    id: str
    name: str
    slug: str
    colour: str


@strawberry.type(name="SupportSla")
class SlaType:
    first_response_due_at: str | None
    resolution_due_at: str | None
    first_response_at: str | None
    first_response_breached: bool
    resolution_breached: bool
    breached: bool


@strawberry.type(name="SupportAttachment")
class AttachmentType:
    id: str
    name: str
    url: str
    content_type: str
    size: int


@strawberry.type(name="SupportMessage")
class MessageType:
    """One message, as this account may read it."""

    id: str
    ticket: str
    author: AccountType | None
    kind: str
    visibility: str
    body: str
    data: JSON
    attachments: list[AttachmentType]
    created_at: str
    edited_at: str | None
    deleted: bool


@strawberry.type(name="SupportParticipant")
class ParticipantType:
    user: AccountType | None
    role: str
    joined_at: str
    last_read_at: str | None
    notify: bool


@strawberry.type(name="SupportTicket")
class TicketType:
    """One thread, without its messages."""

    id: str
    reference: str
    kind: str
    subject: str
    status: str
    priority: str
    client: AccountType | None
    assignee: AccountType | None
    category: CategoryRefType | None
    tags: list[str]
    data: JSON
    created_at: str
    updated_at: str | None
    last_message_at: str | None
    resolved_at: str | None
    closed_at: str | None
    rating: int | None
    rating_comment: str
    sla: SlaType
    unread: int
    participants: list[ParticipantType]


@strawberry.type(name="SupportTicketPage")
class TicketPageType:
    """A page of threads, and how many there were to page through."""

    tickets: list[TicketType]
    total: int
    limit: int
    offset: int


@strawberry.type(name="SupportMessagePage")
class MessagePageType:
    """A page of one thread, oldest first."""

    ticket: str
    messages: list[MessageType]
    total: int
    limit: int
    offset: int


@strawberry.type(name="SupportUnread")
class UnreadType:
    messages: int
    tickets: int


@strawberry.type(name="SupportCannedReply")
class CannedReplyType:
    id: str
    title: str
    body: str
    category: str | None


@strawberry.type(name="SupportStats")
class StatsType:
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
    satisfaction: float | None


@strawberry.type(name="SupportRead")
class ReadType:
    ticket: str
    changed: bool
    unread: int
    last_read_at: str | None


@strawberry.type(name="SupportStatus")
class StatusType:
    ticket: str
    status: str
    changed: bool


@strawberry.type(name="SupportPriority")
class PriorityType:
    ticket: str
    priority: str
    changed: bool


@strawberry.type(name="SupportAssign")
class AssignType:
    ticket: str
    assignee: AccountType | None
    changed: bool


@strawberry.type(name="SupportTags")
class TagsType:
    ticket: str
    tags: list[str]
    changed: bool


@strawberry.type(name="SupportRating")
class RatingType:
    ticket: str
    rating: int
    comment: str


@strawberry.type(name="SupportTyping")
class TypingType:
    ticket: str
    typing: bool


# -- turning a service payload into one of the above ----------------------
#
# The service answers with plain dictionaries, which is what lets one
# implementation serve four transports. These are the adapters, and they are
# deliberately dull: anything clever here would be behaviour one transport had
# and the others did not.


def account_type(row: dict[str, Any] | None) -> AccountType | None:
    if row is None:
        return None
    return AccountType(id=row["id"], username=row["username"], staff=row["staff"])


def sla_type(row: dict[str, Any]) -> SlaType:
    return SlaType(
        first_response_due_at=row["first_response_due_at"],
        resolution_due_at=row["resolution_due_at"],
        first_response_at=row["first_response_at"],
        first_response_breached=row["first_response_breached"],
        resolution_breached=row["resolution_breached"],
        breached=row["breached"],
    )


def attachment_type(row: dict[str, Any]) -> AttachmentType:
    return AttachmentType(
        id=row["id"],
        name=row["name"],
        url=row["url"],
        content_type=row["content_type"],
        size=row["size"],
    )


def message_type(row: dict[str, Any]) -> MessageType:
    return MessageType(
        id=row["id"],
        ticket=row["ticket"],
        author=account_type(row["author"]),
        kind=row["kind"],
        visibility=row["visibility"],
        body=row["body"],
        data=row.get("data", {}),
        attachments=[attachment_type(item) for item in row.get("attachments", [])],
        # Already an ISO string: the payload renders it once, for every reader.
        created_at=row["created_at"],
        edited_at=row["edited_at"],
        deleted=row["deleted"],
    )


def participant_type(row: dict[str, Any]) -> ParticipantType:
    return ParticipantType(
        user=account_type(row["user"]),
        role=row["role"],
        joined_at=row["joined_at"],
        last_read_at=row["last_read_at"],
        notify=row["notify"],
    )


def category_ref(row: dict[str, Any] | None) -> CategoryRefType | None:
    if row is None:
        return None
    return CategoryRefType(id=row["id"], name=row["name"], slug=row["slug"])


def ticket_type(row: dict[str, Any]) -> TicketType:
    return TicketType(
        id=row["id"],
        reference=row["reference"],
        kind=row["kind"],
        subject=row["subject"],
        status=row["status"],
        priority=row["priority"],
        client=account_type(row["client"]),
        assignee=account_type(row["assignee"]),
        category=category_ref(row["category"]),
        tags=row["tags"],
        data=row.get("data", {}),
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        last_message_at=row["last_message_at"],
        resolved_at=row["resolved_at"],
        closed_at=row["closed_at"],
        rating=row["rating"],
        rating_comment=row["rating_comment"],
        sla=sla_type(row["sla"]),
        unread=row.get("unread", 0),
        participants=[participant_type(item) for item in row.get("participants", [])],
    )


def category_type(row: dict[str, Any]) -> CategoryType:
    return CategoryType(
        id=row["id"],
        name=row["name"],
        slug=row["slug"],
        description=row["description"],
        default_priority=row["default_priority"],
        first_response_minutes=row["first_response_minutes"],
        resolution_minutes=row["resolution_minutes"],
    )


def tag_type(row: dict[str, Any]) -> TagType:
    return TagType(id=row["id"], name=row["name"], slug=row["slug"], colour=row["colour"])


def canned_reply_type(row: dict[str, Any]) -> CannedReplyType:
    return CannedReplyType(
        id=row["id"], title=row["title"], body=row["body"], category=row["category"]
    )
