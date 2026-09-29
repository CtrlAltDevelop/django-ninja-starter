"""The support app's contribution to the project's GraphQL schema.

Everything is scoped to the caller by the service, not by an argument: no field
takes a user id, so the only conversations a query can read are the ones the
credential it carried is entitled to.

The fields mirror the REST endpoints and the socket commands one for one, so a
project that publishes all three publishes one contract three ways rather than
three contracts. Names carry a ``support`` prefix where the bare word would be
too general to share a schema with the rest of the project -- ``supportTickets``
rather than ``tickets`` -- because Strawberry merges the apps' root types by
inheritance and two apps claiming one attribute would silently leave one field
where two were meant. See :mod:`config.graph`, which refuses that rather than
publishing it.

The one thing missing here is the upload. A file needs a multipart body, which
is HTTP's; the mutation that *attaches* one takes the id the upload answered
with, so a GraphQL client sends one HTTP request and then stays in GraphQL.
"""

from typing import Any
from uuid import UUID

import strawberry
from strawberry.types import Info

from apps.support.graph.types import (
    AssignType,
    CannedReplyType,
    CategoryType,
    ChannelType,
    LeftType,
    MessagePageType,
    MessageType,
    ParticipantType,
    PriorityType,
    RatingType,
    ReadType,
    StatsType,
    StatusType,
    TagsType,
    TagType,
    TicketPageType,
    TicketType,
    TypingType,
    UnreadType,
    account_type,
    canned_reply_type,
    category_type,
    channel_type,
    message_type,
    participant_type,
    tag_type,
    ticket_type,
)
from apps.support.services import (
    DEFAULT_PAGE,
    InvalidRequest,
    MessageNotFound,
    NotPermitted,
    TicketNotFound,
    support_service,
)
from infrastructure.common.errors import ApiError
from infrastructure.common.graph.errors import require_caller, resolver
from infrastructure.common.identity import caller
from infrastructure.common.responses import ResponseTitle


def _caller(info: Info[Any, Any]) -> Any:
    return require_caller(caller(info.context.request))


def _call(method: Any, *args: Any, **kwargs: Any) -> Any:
    """Run one service call, translating its refusals into GraphQL errors.

    The same four-way translation the REST router does, onto the same titles: a
    client that has learned what ``NOT_FOUND`` means from one transport does not
    learn it again here. ``ValueError`` is caught alongside
    :class:`InvalidRequest` because a malformed UUID arrives as one, and "that
    is not an id" and "that is not a status" are the same class of mistake.
    """
    try:
        return method(*args, **kwargs)
    except (TicketNotFound, MessageNotFound) as missing:
        raise ApiError(str(missing), status=404, title=ResponseTitle.NOT_FOUND) from missing
    except NotPermitted as refused:
        raise ApiError(str(refused), status=403, title=ResponseTitle.FORBIDDEN) from refused
    except (InvalidRequest, ValueError) as invalid:
        raise ApiError(str(invalid), status=400, title=ResponseTitle.BAD_REQUEST) from invalid


def _uuid(value: str, what: str = "ticket") -> UUID:
    try:
        return UUID(value)
    except (TypeError, ValueError) as error:
        raise ApiError(
            f"That is not a {what} id.", status=400, title=ResponseTitle.BAD_REQUEST
        ) from error


@strawberry.type
class Query:
    @strawberry.field(
        description="Support conversations this account can see, newest activity first."
    )
    @resolver
    def support_tickets(
        self,
        info: Info[Any, Any],
        status: str | None = None,
        kind: str | None = None,
        priority: str | None = None,
        category: str | None = None,
        assignee: str | None = None,
        mine: bool = False,
        unassigned: bool = False,
        live: bool | None = None,
        breached: bool | None = None,
        search: str | None = None,
        limit: int = DEFAULT_PAGE,
        offset: int = 0,
    ) -> TicketPageType:
        filters: dict[str, Any] = {
            "status": status,
            "kind": kind,
            "priority": priority,
            "category": category,
            "assignee": assignee,
            "mine": mine,
            "unassigned": unassigned,
            "live": live,
            "search": search,
        }
        user = _caller(info)
        rows = _call(
            support_service.tickets,
            user,
            limit=limit,
            offset=offset,
            breached=breached,
            **filters,
        )
        return TicketPageType(
            tickets=[ticket_type(row) for row in rows],
            total=_call(support_service.count, user, **filters),
            limit=limit,
            offset=offset,
        )

    @strawberry.field(description="One support conversation, with who is in it.")
    @resolver
    def support_ticket(self, info: Info[Any, Any], ticket_id: str) -> TicketType:
        return ticket_type(_call(support_service.ticket, _caller(info), _uuid(ticket_id)))

    @strawberry.field(description="A page of one conversation, oldest first.")
    @resolver
    def support_messages(
        self,
        info: Info[Any, Any],
        ticket_id: str,
        limit: int = DEFAULT_PAGE,
        offset: int = 0,
    ) -> MessagePageType:
        page = _call(
            support_service.messages,
            _caller(info),
            _uuid(ticket_id),
            limit=limit,
            offset=offset,
        )
        return MessagePageType(
            ticket=page["ticket"],
            messages=[message_type(row) for row in page["messages"]],
            total=page["total"],
            limit=page["limit"],
            offset=page["offset"],
        )

    @strawberry.field(description="How many support messages are waiting, and in how many threads.")
    @resolver
    def support_unread(self, info: Info[Any, Any]) -> UnreadType:
        result = support_service.unread(_caller(info))
        return UnreadType(messages=result["messages"], tickets=result["tickets"])

    @strawberry.field(description="What a ticket can be about, and the response times promised.")
    @resolver
    def support_categories(self, info: Info[Any, Any]) -> list[CategoryType]:
        _caller(info)
        return [category_type(row) for row in support_service.categories()]

    @strawberry.field(
        description=(
            "Every open channel, joined or not. The only support listing that "
            "shows you something you are not already part of."
        )
    )
    @resolver
    def support_channels(self, info: Info[Any, Any], search: str = "") -> list[ChannelType]:
        return [
            channel_type(row)
            for row in _call(support_service.channels, _caller(info), search=search)
        ]

    @strawberry.field(description="The desk's own tags. Staff only.")
    @resolver
    def support_tags(self, info: Info[Any, Any]) -> list[TagType]:
        return [tag_type(row) for row in _call(support_service.tags, _caller(info))]

    @strawberry.field(description="The desk's saved replies. Staff only.")
    @resolver
    def support_canned_replies(
        self, info: Info[Any, Any], category: str | None = None
    ) -> list[CannedReplyType]:
        rows = _call(support_service.canned_replies, _caller(info), category=category)
        return [canned_reply_type(row) for row in rows]

    @strawberry.field(description="The numbers the desk runs on. Staff only.")
    @resolver
    def support_stats(self, info: Info[Any, Any]) -> StatsType:
        return StatsType(**_call(support_service.stats, _caller(info)))


@strawberry.type
class Mutation:
    @strawberry.mutation(description="Open a support conversation, with its first message.")
    @resolver
    def open_support_ticket(
        self,
        info: Info[Any, Any],
        subject: str = "",
        body: str = "",
        kind: str = "ticket",
        category: str | None = None,
        priority: str = "",
        upload_ids: list[str] | None = None,
        data: strawberry.scalars.JSON | None = None,
    ) -> TicketType:
        return ticket_type(
            _call(
                support_service.open,
                _caller(info),
                subject=subject,
                body=body,
                kind=kind,
                category=category,
                priority=priority,
                upload_ids=[_uuid(item, "upload") for item in upload_ids or []],
                data=data or {},
            )
        )

    @strawberry.mutation(description="Say something in a support conversation.")
    @resolver
    def send_support_message(
        self,
        info: Info[Any, Any],
        ticket_id: str,
        body: str = "",
        upload_ids: list[str] | None = None,
        internal: bool = False,
        data: strawberry.scalars.JSON | None = None,
    ) -> MessageType:
        return message_type(
            _call(
                support_service.send,
                _caller(info),
                _uuid(ticket_id),
                body,
                upload_ids=[_uuid(item, "upload") for item in upload_ids or []],
                internal=internal,
                data=data or {},
            )
        )

    @strawberry.mutation(description="Rewrite your own support message.")
    @resolver
    def edit_support_message(self, info: Info[Any, Any], message_id: str, body: str) -> MessageType:
        return message_type(
            _call(support_service.edit, _caller(info), _uuid(message_id, "message"), body)
        )

    @strawberry.mutation(description="Retract a support message, leaving a tombstone.")
    @resolver
    def delete_support_message(self, info: Info[Any, Any], message_id: str) -> MessageType:
        return message_type(
            _call(support_service.delete, _caller(info), _uuid(message_id, "message"))
        )

    @strawberry.mutation(description="Mark a support conversation read up to now.")
    @resolver
    def read_support_ticket(self, info: Info[Any, Any], ticket_id: str) -> ReadType:
        return ReadType(**_call(support_service.read, _caller(info), _uuid(ticket_id)))

    @strawberry.mutation(description="Put a whole support conversation back in the badge.")
    @resolver
    def unread_support_ticket(self, info: Info[Any, Any], ticket_id: str) -> ReadType:
        return ReadType(**_call(support_service.unread_ticket, _caller(info), _uuid(ticket_id)))

    @strawberry.mutation(description="Move a support conversation to a status.")
    @resolver
    def set_support_status(self, info: Info[Any, Any], ticket_id: str, status: str) -> StatusType:
        return StatusType(**_call(support_service.status, _caller(info), _uuid(ticket_id), status))

    @strawberry.mutation(description="Close a support conversation.")
    @resolver
    def close_support_ticket(self, info: Info[Any, Any], ticket_id: str) -> StatusType:
        return StatusType(**_call(support_service.close, _caller(info), _uuid(ticket_id)))

    @strawberry.mutation(description="Reopen a settled support conversation.")
    @resolver
    def reopen_support_ticket(self, info: Info[Any, Any], ticket_id: str) -> StatusType:
        return StatusType(**_call(support_service.reopen, _caller(info), _uuid(ticket_id)))

    @strawberry.mutation(description="Give a support conversation to an agent. Staff only.")
    @resolver
    def assign_support_ticket(
        self, info: Info[Any, Any], ticket_id: str, agent: str | None = None
    ) -> AssignType:
        result = _call(
            support_service.assign,
            _caller(info),
            _uuid(ticket_id),
            _uuid(agent, "account") if agent else None,
        )
        return AssignType(
            ticket=result["ticket"],
            assignee=account_type(result["assignee"]),
            changed=result["changed"],
        )

    @strawberry.mutation(
        description="Take an unassigned support conversation yourself. Staff only."
    )
    @resolver
    def claim_support_ticket(self, info: Info[Any, Any], ticket_id: str) -> AssignType:
        result = _call(support_service.claim, _caller(info), _uuid(ticket_id))
        return AssignType(
            ticket=result["ticket"],
            assignee=account_type(result["assignee"]),
            changed=result["changed"],
        )

    @strawberry.mutation(description="Reprioritise a support conversation. Staff only.")
    @resolver
    def set_support_priority(
        self, info: Info[Any, Any], ticket_id: str, priority: str
    ) -> PriorityType:
        return PriorityType(
            **_call(support_service.priority, _caller(info), _uuid(ticket_id), priority)
        )

    @strawberry.mutation(description="Replace a support conversation's tags. Staff only.")
    @resolver
    def tag_support_ticket(self, info: Info[Any, Any], ticket_id: str, tags: list[str]) -> TagsType:
        return TagsType(**_call(support_service.tag, _caller(info), _uuid(ticket_id), tags))

    @strawberry.mutation(description="Add somebody to a support conversation. Staff only.")
    @resolver
    def invite_to_support_ticket(
        self, info: Info[Any, Any], ticket_id: str, account: str, role: str = "observer"
    ) -> ParticipantType:
        return participant_type(
            _call(
                support_service.invite,
                _caller(info),
                _uuid(ticket_id),
                _uuid(account, "account"),
                role,
            )
        )

    @strawberry.mutation(description="Rate a settled support conversation. The client's only.")
    @resolver
    def rate_support_ticket(
        self, info: Info[Any, Any], ticket_id: str, score: int, comment: str = ""
    ) -> RatingType:
        result = _call(support_service.rate, _caller(info), _uuid(ticket_id), score, comment)
        return RatingType(
            ticket=result["ticket"], rating=result["rating"], comment=result["comment"]
        )

    @strawberry.mutation(description="Open a channel anybody signed in may find and join.")
    @resolver
    def create_support_channel(
        self, info: Info[Any, Any], name: str, slug: str = "", body: str = ""
    ) -> TicketType:
        return ticket_type(
            _call(support_service.create_channel, _caller(info), name, slug=slug, body=body)
        )

    @strawberry.mutation(
        description="Open a private group. Invisible to everybody but its members."
    )
    @resolver
    def create_support_group(
        self, info: Info[Any, Any], name: str, members: list[str], body: str = ""
    ) -> TicketType:
        return ticket_type(
            _call(
                support_service.create_group,
                _caller(info),
                name,
                [_uuid(member, "account") for member in members],
                body=body,
            )
        )

    @strawberry.mutation(
        description="Open the private chat with one account, or return the existing one."
    )
    @resolver
    def open_support_direct(self, info: Info[Any, Any], account: str) -> TicketType:
        return ticket_type(_call(support_service.direct, _caller(info), _uuid(account, "account")))

    @strawberry.mutation(description="Join a channel. A channel only, and idempotent.")
    @resolver
    def join_support_room(self, info: Info[Any, Any], ticket_id: str) -> ParticipantType:
        return participant_type(_call(support_service.join_room, _caller(info), _uuid(ticket_id)))

    @strawberry.mutation(description="Leave a channel or a group. A private chat cannot be left.")
    @resolver
    def leave_support_room(self, info: Info[Any, Any], ticket_id: str) -> LeftType:
        return LeftType(**_call(support_service.leave_room, _caller(info), _uuid(ticket_id)))

    @strawberry.mutation(description="Say you are typing in a support conversation.")
    @resolver
    def support_typing(
        self, info: Info[Any, Any], ticket_id: str, typing: bool = True
    ) -> TypingType:
        return TypingType(**_call(support_service.typing, _caller(info), _uuid(ticket_id), typing))
