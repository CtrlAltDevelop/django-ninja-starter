"""Support over gRPC: the same conversations, for a service that is not a browser.

Scoped to the caller by the service layer, not by a request field: no call takes
a user id, so the only conversations a call can read are the ones the credential
in its metadata is entitled to.

The calls mirror the REST endpoints and the socket commands one for one. The one
endpoint with no equivalent here is the upload, and that is a protocol
consequence rather than a decision: a file needs a multipart body. A gRPC client
uploads over HTTP and then names the id, which is exactly what a socket client
does.
"""

import json
from typing import Any
from uuid import UUID

from asgiref.sync import sync_to_async
from django_socio_grpc import generics
from django_socio_grpc.decorators import grpc_action

from apps.support.grpc.serializers import (
    Account,
    CannedReply,
    Category,
    Channel,
    Message,
    Participant,
    Tag,
    Ticket,
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
from infrastructure.common.grpc.errors import action, require_caller
from infrastructure.common.identity import grpc_caller
from infrastructure.common.responses import ResponseTitle

#: The queue filters. Three-valued flags are ``optional`` on purpose: unset
#: means "either", which is not the same question as ``live = false``. Shared by
#: List and Count, which take the same filters for the same reason the REST
#: endpoints do.
FILTER_FIELDS = [
    {"name": "status", "type": "string"},
    {"name": "kind", "type": "string"},
    {"name": "priority", "type": "string"},
    {"name": "category", "type": "string"},
    {"name": "assignee", "type": "string"},
    {"name": "mine", "type": "bool"},
    {"name": "unassigned", "type": "bool"},
    {"name": "live", "cardinality": "optional", "type": "bool"},
    {"name": "search", "type": "string"},
]

TICKET_REQUEST = [{"name": "ticket_id", "type": "string"}]
MESSAGE_REQUEST = [{"name": "message_id", "type": "string"}]


def _pb2() -> Any:
    from apps.support.grpc import support_pb2

    return support_pb2


def _text(value: Any) -> str:
    """A nullable string as protobuf carries it. See the serializers module."""
    return "" if value is None else str(value)


def _account(row: dict[str, Any] | None) -> Any:
    if row is None:
        return None
    return {"id": row["id"], "username": row["username"], "staff": row["staff"]}


def _sla(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "first_response_due_at": _text(row["first_response_due_at"]),
        "resolution_due_at": _text(row["resolution_due_at"]),
        "first_response_at": _text(row["first_response_at"]),
        "first_response_breached": row["first_response_breached"],
        "resolution_breached": row["resolution_breached"],
        "breached": row["breached"],
    }


def _attachment(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": row["id"],
        "name": row["name"],
        "url": row["url"],
        "content_type": row["content_type"],
        "size": row["size"],
    }


def _message_fields(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": row["id"],
        "ticket": row["ticket"],
        "author": _account(row["author"]),
        "kind": row["kind"],
        "visibility": row["visibility"],
        "body": row["body"],
        "data_json": json.dumps(row.get("data", {}), default=str),
        "attachments": [_attachment(item) for item in row.get("attachments", [])],
        "created_at": row["created_at"],
        "edited_at": _text(row["edited_at"]),
        "deleted": row["deleted"],
    }


def _participant(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "user": _account(row["user"]),
        "role": row["role"],
        "joined_at": row["joined_at"],
        "last_read_at": _text(row["last_read_at"]),
        "notify": row["notify"],
    }


def _category_ref(row: dict[str, Any] | None) -> Any:
    return None if row is None else {"id": row["id"], "name": row["name"], "slug": row["slug"]}


def _ticket_fields(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": row["id"],
        "reference": row["reference"],
        "kind": row["kind"],
        "subject": row["subject"],
        "slug": row.get("slug", ""),
        "status": row["status"],
        "priority": row["priority"],
        "client": _account(row["client"]),
        "assignee": _account(row["assignee"]),
        "category": _category_ref(row["category"]),
        "tags": row["tags"],
        "data_json": json.dumps(row.get("data", {}), default=str),
        "created_at": row["created_at"],
        "updated_at": _text(row["updated_at"]),
        "last_message_at": _text(row["last_message_at"]),
        "resolved_at": _text(row["resolved_at"]),
        "closed_at": _text(row["closed_at"]),
        # proto3 has no null int32, and zero stars is not a rating anybody can
        # give -- so an unrated thread comes back as 0 and a client reads that
        # as "not rated", which is the only value it can mean.
        "rating": row["rating"] or 0,
        "rating_comment": row["rating_comment"],
        "sla": _sla(row["sla"]),
        "unread": row.get("unread", 0),
        "participants": [_participant(item) for item in row.get("participants", [])],
    }


def _message(row: dict[str, Any], name: str = "Message") -> Any:
    """One message as whichever of the generated shapes this RPC answers with.

    django-socio-grpc emits a separate ``...Response`` message for every nested
    read type, so ``Message.author`` and ``SentMessage.author`` are different
    protobuf classes carrying identical fields. Building the fields as a plain
    dict and letting protobuf construct the nested messages is what lets one
    function serve all of them -- an instance of the wrong twin is rejected,
    a dict is not.
    """
    return getattr(_pb2(), name)(**_message_fields(row))


def _ticket(row: dict[str, Any], name: str = "Ticket") -> Any:
    """One thread, as whichever of the generated shapes this RPC answers with."""
    return getattr(_pb2(), name)(**_ticket_fields(row))


def _filters(request: Any) -> dict[str, Any]:
    return {
        "status": request.status or None,
        "kind": request.kind or None,
        "priority": request.priority or None,
        "category": request.category or None,
        "assignee": request.assignee or None,
        "mine": request.mine,
        "unassigned": request.unassigned,
        "live": request.live if request.HasField("live") else None,
        "search": request.search or None,
    }


def _identifier(value: str, what: str = "ticket") -> UUID:
    try:
        return UUID(value)
    except (TypeError, ValueError) as error:
        raise ApiError(
            f"That is not a {what} id.", status=400, title=ResponseTitle.BAD_REQUEST
        ) from error


def _json(raw: str) -> dict[str, Any]:
    """Read the free-form ``data_json`` a request carried.

    Empty is an empty object rather than an error: a caller with nothing to
    attach should not have to send ``"{}"``. Anything that is not JSON, or is
    JSON but not an object, is refused -- silently dropping it would store
    nothing and report success.
    """
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as error:
        raise ApiError(
            "`data_json` is not JSON.", status=400, title=ResponseTitle.BAD_REQUEST
        ) from error
    if not isinstance(parsed, dict):
        raise ApiError(
            "`data_json` has to be a JSON object.",
            status=400,
            title=ResponseTitle.BAD_REQUEST,
        )
    return parsed


def _read(result: dict[str, Any]) -> dict[str, Any]:
    """The read-state reply, with its nullable timestamp flattened for protobuf."""
    return {
        "ticket": result["ticket"],
        "changed": result["changed"],
        "unread": result["unread"],
        "last_read_at": _text(result["last_read_at"]),
    }


async def _call(method: Any, *args: Any, **kwargs: Any) -> Any:
    """Run one service call off the event loop, translating its refusals.

    ``sync_to_async`` because the whole service layer is ordinary synchronous
    Django and the gRPC server is asyncio: the ORM refuses to be called from the
    loop. The translation is the same four-way mapping the REST router and the
    GraphQL resolvers do, onto the same titles.
    """
    try:
        return await sync_to_async(method)(*args, **kwargs)
    except (TicketNotFound, MessageNotFound) as missing:
        raise ApiError(str(missing), status=404, title=ResponseTitle.NOT_FOUND) from missing
    except NotPermitted as refused:
        raise ApiError(str(refused), status=403, title=ResponseTitle.FORBIDDEN) from refused
    except InvalidRequest as invalid:
        raise ApiError(str(invalid), status=400, title=ResponseTitle.BAD_REQUEST) from invalid


class SupportService(generics.GenericService):
    """The same calls the REST router publishes under `/support`."""

    # -- reading ----------------------------------------------------------

    @grpc_action(
        request=[
            *FILTER_FIELDS,
            {"name": "breached", "cardinality": "optional", "type": "bool"},
            {"name": "limit", "type": "int32"},
            {"name": "offset", "type": "int32"},
        ],
        request_name="ListRequest",
        response=[
            {"name": "tickets", "cardinality": "repeated", "type": Ticket},
            {"name": "total", "type": "int32"},
            {"name": "limit", "type": "int32"},
            {"name": "offset", "type": "int32"},
        ],
        response_name="TicketList",
    )
    @action
    async def List(self, request: Any, context: Any) -> Any:
        user = require_caller(await grpc_caller(context))
        filters = _filters(request)
        limit = request.limit or DEFAULT_PAGE
        rows = await _call(
            support_service.tickets,
            user,
            limit=limit,
            offset=request.offset,
            breached=request.breached if request.HasField("breached") else None,
            **filters,
        )
        total = await _call(support_service.count, user, **filters)
        return _pb2().TicketList(
            tickets=[_ticket(row) for row in rows],
            total=total,
            limit=limit,
            offset=request.offset,
        )

    @grpc_action(
        request=FILTER_FIELDS,
        request_name="CountRequest",
        response=[{"name": "count", "type": "int32"}],
        response_name="CountResult",
    )
    @action
    async def Count(self, request: Any, context: Any) -> Any:
        user = require_caller(await grpc_caller(context))
        count = await _call(support_service.count, user, **_filters(request))
        return _pb2().CountResult(count=count)

    @grpc_action(
        request=TICKET_REQUEST,
        request_name="GetRequest",
        response=Ticket,
        response_name="Ticket",
    )
    @action
    async def Get(self, request: Any, context: Any) -> Any:
        user = require_caller(await grpc_caller(context))
        row = await _call(support_service.ticket, user, _identifier(request.ticket_id))
        return _ticket(row)

    @grpc_action(
        request=[
            *TICKET_REQUEST,
            {"name": "limit", "type": "int32"},
            {"name": "offset", "type": "int32"},
        ],
        request_name="MessagesRequest",
        response=[
            {"name": "ticket", "type": "string"},
            {"name": "messages", "cardinality": "repeated", "type": Message},
            {"name": "total", "type": "int32"},
            {"name": "limit", "type": "int32"},
            {"name": "offset", "type": "int32"},
        ],
        response_name="MessageList",
    )
    @action
    async def Messages(self, request: Any, context: Any) -> Any:
        user = require_caller(await grpc_caller(context))
        limit = request.limit or DEFAULT_PAGE
        page = await _call(
            support_service.messages,
            user,
            _identifier(request.ticket_id),
            limit=limit,
            offset=request.offset,
        )
        return _pb2().MessageList(
            ticket=page["ticket"],
            messages=[_message(row) for row in page["messages"]],
            total=page["total"],
            limit=page["limit"],
            offset=page["offset"],
        )

    @grpc_action(
        request=[],
        response=[
            {"name": "messages", "type": "int32"},
            {"name": "tickets", "type": "int32"},
        ],
        response_name="UnreadResult",
    )
    @action
    async def Unread(self, request: Any, context: Any) -> Any:
        user = require_caller(await grpc_caller(context))
        result = await _call(support_service.unread, user)
        return _pb2().UnreadResult(messages=result["messages"], tickets=result["tickets"])

    @grpc_action(
        request=[],
        response=[{"name": "categories", "cardinality": "repeated", "type": Category}],
        response_name="CategoryList",
    )
    @action
    async def Categories(self, request: Any, context: Any) -> Any:
        require_caller(await grpc_caller(context))
        rows = await _call(support_service.categories)
        return _pb2().CategoryList(categories=[_pb2().Category(**row) for row in rows])

    @grpc_action(
        request=[],
        response=[{"name": "tags", "cardinality": "repeated", "type": Tag}],
        response_name="TagList",
    )
    @action
    async def Tags(self, request: Any, context: Any) -> Any:
        user = require_caller(await grpc_caller(context))
        rows = await _call(support_service.tags, user)
        return _pb2().TagList(tags=[_pb2().Tag(**row) for row in rows])

    @grpc_action(
        request=[{"name": "category", "type": "string"}],
        request_name="CannedRequest",
        response=[{"name": "replies", "cardinality": "repeated", "type": CannedReply}],
        response_name="CannedList",
    )
    @action
    async def Canned(self, request: Any, context: Any) -> Any:
        user = require_caller(await grpc_caller(context))
        rows = await _call(support_service.canned_replies, user, category=request.category or None)
        return _pb2().CannedList(
            replies=[
                _pb2().CannedReply(
                    id=row["id"],
                    title=row["title"],
                    body=row["body"],
                    category=_text(row["category"]),
                )
                for row in rows
            ]
        )

    @grpc_action(
        request=[],
        response=[
            {"name": "total", "type": "int32"},
            {"name": "open", "type": "int32"},
            {"name": "pending", "type": "int32"},
            {"name": "on_hold", "type": "int32"},
            {"name": "resolved", "type": "int32"},
            {"name": "closed", "type": "int32"},
            {"name": "unassigned", "type": "int32"},
            {"name": "chats", "type": "int32"},
            {"name": "rated", "type": "int32"},
            {"name": "breached", "type": "int32"},
            {"name": "awaiting_first_response", "type": "int32"},
            {"name": "satisfaction", "type": "float"},
        ],
        response_name="StatsResult",
    )
    @action
    async def Stats(self, request: Any, context: Any) -> Any:
        user = require_caller(await grpc_caller(context))
        stats = await _call(support_service.stats, user)
        return _pb2().StatsResult(**{**stats, "satisfaction": stats["satisfaction"] or 0.0})

    # -- opening and talking ----------------------------------------------

    @grpc_action(
        request=[
            {"name": "subject", "type": "string"},
            {"name": "body", "type": "string"},
            {"name": "kind", "type": "string"},
            {"name": "category", "type": "string"},
            {"name": "priority", "type": "string"},
            {"name": "upload_ids", "cardinality": "repeated", "type": "string"},
            {"name": "data_json", "type": "string"},
        ],
        request_name="OpenRequest",
        response=Ticket,
        response_name="OpenedTicket",
    )
    @action
    async def Open(self, request: Any, context: Any) -> Any:
        user = require_caller(await grpc_caller(context))
        row = await _call(
            support_service.open,
            user,
            subject=request.subject,
            body=request.body,
            kind=request.kind or "ticket",
            category=request.category or None,
            priority=request.priority,
            upload_ids=[_identifier(item, "upload") for item in request.upload_ids],
            data=_json(request.data_json),
        )
        return _ticket(row, "OpenedTicket")

    @grpc_action(
        request=[
            *TICKET_REQUEST,
            {"name": "body", "type": "string"},
            {"name": "upload_ids", "cardinality": "repeated", "type": "string"},
            {"name": "internal", "type": "bool"},
            {"name": "data_json", "type": "string"},
        ],
        request_name="SendRequest",
        response=Message,
        response_name="SentMessage",
    )
    @action
    async def Send(self, request: Any, context: Any) -> Any:
        user = require_caller(await grpc_caller(context))
        row = await _call(
            support_service.send,
            user,
            _identifier(request.ticket_id),
            request.body,
            upload_ids=[_identifier(item, "upload") for item in request.upload_ids],
            internal=request.internal,
            data=_json(request.data_json),
        )
        return _message(row, "SentMessage")

    @grpc_action(
        request=[*MESSAGE_REQUEST, {"name": "body", "type": "string"}],
        request_name="EditRequest",
        response=Message,
        response_name="EditedMessage",
    )
    @action
    async def Edit(self, request: Any, context: Any) -> Any:
        user = require_caller(await grpc_caller(context))
        row = await _call(
            support_service.edit, user, _identifier(request.message_id, "message"), request.body
        )
        return _message(row, "EditedMessage")

    @grpc_action(
        request=MESSAGE_REQUEST,
        request_name="DeleteRequest",
        response=Message,
        response_name="DeletedMessage",
    )
    @action
    async def Delete(self, request: Any, context: Any) -> Any:
        user = require_caller(await grpc_caller(context))
        row = await _call(support_service.delete, user, _identifier(request.message_id, "message"))
        return _message(row, "DeletedMessage")

    # -- state ------------------------------------------------------------

    @grpc_action(
        request=TICKET_REQUEST,
        request_name="ReadRequest",
        response=[
            {"name": "ticket", "type": "string"},
            {"name": "changed", "type": "bool"},
            {"name": "unread", "type": "int32"},
            {"name": "last_read_at", "type": "string"},
        ],
        response_name="ReadResult",
    )
    @action
    async def Read(self, request: Any, context: Any) -> Any:
        user = require_caller(await grpc_caller(context))
        return _pb2().ReadResult(
            **_read(await _call(support_service.read, user, _identifier(request.ticket_id)))
        )

    @grpc_action(
        request=TICKET_REQUEST,
        request_name="UnreadTicketRequest",
        response=[
            {"name": "ticket", "type": "string"},
            {"name": "changed", "type": "bool"},
            {"name": "unread", "type": "int32"},
            {"name": "last_read_at", "type": "string"},
        ],
        response_name="UnreadTicketResult",
    )
    @action
    async def UnreadTicket(self, request: Any, context: Any) -> Any:
        user = require_caller(await grpc_caller(context))
        return _pb2().UnreadTicketResult(
            **_read(
                await _call(support_service.unread_ticket, user, _identifier(request.ticket_id))
            )
        )

    @grpc_action(
        request=[*TICKET_REQUEST, {"name": "status", "type": "string"}],
        request_name="StatusRequest",
        response=[
            {"name": "ticket", "type": "string"},
            {"name": "status", "type": "string"},
            {"name": "changed", "type": "bool"},
        ],
        response_name="StatusResult",
    )
    @action
    async def Status(self, request: Any, context: Any) -> Any:
        user = require_caller(await grpc_caller(context))
        result = await _call(
            support_service.status, user, _identifier(request.ticket_id), request.status
        )
        return _pb2().StatusResult(**result)

    @grpc_action(
        request=[*TICKET_REQUEST, {"name": "agent", "type": "string"}],
        request_name="AssignRequest",
        response=[
            {"name": "ticket", "type": "string"},
            {"name": "assignee", "type": Account},
            {"name": "changed", "type": "bool"},
        ],
        response_name="AssignResult",
    )
    @action
    async def Assign(self, request: Any, context: Any) -> Any:
        user = require_caller(await grpc_caller(context))
        result = await _call(
            support_service.assign,
            user,
            _identifier(request.ticket_id),
            _identifier(request.agent, "account") if request.agent else None,
        )
        return _pb2().AssignResult(
            ticket=result["ticket"],
            assignee=_account(result["assignee"]),
            changed=result["changed"],
        )

    @grpc_action(
        request=TICKET_REQUEST,
        request_name="ClaimRequest",
        response=[
            {"name": "ticket", "type": "string"},
            {"name": "assignee", "type": Account},
            {"name": "changed", "type": "bool"},
        ],
        response_name="ClaimResult",
    )
    @action
    async def Claim(self, request: Any, context: Any) -> Any:
        user = require_caller(await grpc_caller(context))
        result = await _call(support_service.claim, user, _identifier(request.ticket_id))
        return _pb2().ClaimResult(
            ticket=result["ticket"],
            assignee=_account(result["assignee"]),
            changed=result["changed"],
        )

    @grpc_action(
        request=[*TICKET_REQUEST, {"name": "priority", "type": "string"}],
        request_name="PriorityRequest",
        response=[
            {"name": "ticket", "type": "string"},
            {"name": "priority", "type": "string"},
            {"name": "changed", "type": "bool"},
        ],
        response_name="PriorityResult",
    )
    @action
    async def Priority(self, request: Any, context: Any) -> Any:
        user = require_caller(await grpc_caller(context))
        result = await _call(
            support_service.priority, user, _identifier(request.ticket_id), request.priority
        )
        return _pb2().PriorityResult(**result)

    @grpc_action(
        request=[
            *TICKET_REQUEST,
            {"name": "tags", "cardinality": "repeated", "type": "string"},
        ],
        request_name="TagRequest",
        response=[
            {"name": "ticket", "type": "string"},
            {"name": "tags", "cardinality": "repeated", "type": "string"},
            {"name": "changed", "type": "bool"},
        ],
        response_name="TagResult",
    )
    @action
    async def Tag(self, request: Any, context: Any) -> Any:
        user = require_caller(await grpc_caller(context))
        result = await _call(
            support_service.tag, user, _identifier(request.ticket_id), list(request.tags)
        )
        return _pb2().TagResult(**result)

    @grpc_action(
        request=[
            *TICKET_REQUEST,
            {"name": "account", "type": "string"},
            {"name": "role", "type": "string"},
        ],
        request_name="InviteRequest",
        response=Participant,
        response_name="InvitedParticipant",
    )
    @action
    async def Invite(self, request: Any, context: Any) -> Any:
        user = require_caller(await grpc_caller(context))
        row = await _call(
            support_service.invite,
            user,
            _identifier(request.ticket_id),
            _identifier(request.account, "account"),
            request.role or "observer",
        )
        return _pb2().InvitedParticipant(**_participant(row))

    @grpc_action(
        request=[
            *TICKET_REQUEST,
            {"name": "score", "type": "int32"},
            {"name": "comment", "type": "string"},
        ],
        request_name="RateRequest",
        response=[
            {"name": "ticket", "type": "string"},
            {"name": "rating", "type": "int32"},
            {"name": "comment", "type": "string"},
        ],
        response_name="RateResult",
    )
    @action
    async def Rate(self, request: Any, context: Any) -> Any:
        user = require_caller(await grpc_caller(context))
        result = await _call(
            support_service.rate,
            user,
            _identifier(request.ticket_id),
            request.score,
            request.comment,
        )
        return _pb2().RateResult(**result)

    @grpc_action(
        request=[*TICKET_REQUEST, {"name": "typing", "type": "bool"}],
        request_name="TypingRequest",
        response=[
            {"name": "ticket", "type": "string"},
            {"name": "typing", "type": "bool"},
        ],
        response_name="TypingResult",
    )
    @action
    async def Typing(self, request: Any, context: Any) -> Any:
        user = require_caller(await grpc_caller(context))
        result = await _call(
            support_service.typing, user, _identifier(request.ticket_id), request.typing
        )
        return _pb2().TypingResult(**result)

    # -- rooms ------------------------------------------------------------

    @grpc_action(
        request=[{"name": "search", "type": "string"}],
        request_name="ChannelsRequest",
        response=[{"name": "channels", "cardinality": "repeated", "type": Channel}],
        response_name="ChannelList",
    )
    @action
    async def Channels(self, request: Any, context: Any) -> Any:
        """Every open channel, joined or not. The one listing that shows you a
        room you are not already in."""
        user = require_caller(await grpc_caller(context))
        rows = await _call(support_service.channels, user, search=request.search)
        return _pb2().ChannelList(
            channels=[
                _pb2().Channel(**_ticket_fields(row), joined=row["joined"], members=row["members"])
                for row in rows
            ]
        )

    @grpc_action(
        request=[
            {"name": "name", "type": "string"},
            {"name": "slug", "type": "string"},
            {"name": "body", "type": "string"},
        ],
        request_name="CreateChannelRequest",
        response=Ticket,
        response_name="Ticket",
    )
    @action
    async def CreateChannel(self, request: Any, context: Any) -> Any:
        user = require_caller(await grpc_caller(context))
        row = await _call(
            support_service.create_channel,
            user,
            request.name,
            slug=request.slug,
            body=request.body,
        )
        return _ticket(row)

    @grpc_action(
        request=[
            {"name": "name", "type": "string"},
            {"name": "members", "cardinality": "repeated", "type": "string"},
            {"name": "body", "type": "string"},
        ],
        request_name="CreateGroupRequest",
        response=Ticket,
        response_name="Ticket",
    )
    @action
    async def CreateGroup(self, request: Any, context: Any) -> Any:
        user = require_caller(await grpc_caller(context))
        row = await _call(
            support_service.create_group,
            user,
            request.name,
            [_identifier(member, "account") for member in request.members],
            body=request.body,
        )
        return _ticket(row)

    @grpc_action(
        request=[{"name": "account", "type": "string"}],
        request_name="DirectRequest",
        response=Ticket,
        response_name="Ticket",
    )
    @action
    async def Direct(self, request: Any, context: Any) -> Any:
        """The private chat with one account, or the one that already existed."""
        user = require_caller(await grpc_caller(context))
        row = await _call(support_service.direct, user, _identifier(request.account, "account"))
        return _ticket(row)

    @grpc_action(
        request=TICKET_REQUEST,
        request_name="JoinRequest",
        response=Participant,
        response_name="JoinedParticipant",
    )
    @action
    async def Join(self, request: Any, context: Any) -> Any:
        user = require_caller(await grpc_caller(context))
        row = await _call(support_service.join_room, user, _identifier(request.ticket_id))
        return _pb2().JoinedParticipant(**_participant(row))

    @grpc_action(
        request=TICKET_REQUEST,
        request_name="LeaveRequest",
        response=[
            {"name": "ticket", "type": "string"},
            {"name": "left", "type": "bool"},
        ],
        response_name="LeaveResult",
    )
    @action
    async def Leave(self, request: Any, context: Any) -> Any:
        user = require_caller(await grpc_caller(context))
        result = await _call(support_service.leave_room, user, _identifier(request.ticket_id))
        return _pb2().LeaveResult(**result)


GRPC_SERVICES = [SupportService]
