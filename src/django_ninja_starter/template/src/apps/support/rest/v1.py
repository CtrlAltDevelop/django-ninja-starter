"""Support over HTTP: the client's conversations, and the desk's queue.

Every decision is :class:`~apps.support.services.SupportService`'s -- including
the ones that matter, which are that the queryset starts from the caller and no
parameter can widen it, and that an internal note is dropped from anything a
client can read.

The endpoints mirror the socket's commands one for one, under the same names and
with the same replies, so a client can move between the two without a second
mental model. The one thing that exists only here is the upload: a WebSocket
frame is JSON and cannot carry a multipart body, so a file is sent over HTTP and
the message that carries it goes over whichever transport the client prefers.
"""

from typing import Any
from uuid import UUID

from django.core.exceptions import ValidationError
from django.http import HttpRequest
from ninja import File, Router
from ninja.errors import HttpError
from ninja.files import UploadedFile

from apps.support.models import Kind, Priority, Status
from apps.support.rest.schemas import (
    AssignIn,
    AssignOut,
    CannedReplyOut,
    CategoryOut,
    ChannelIn,
    ChannelOut,
    DirectIn,
    EditIn,
    GroupIn,
    InviteIn,
    LeftOut,
    MessageIn,
    MessageOut,
    MessagePage,
    OpenIn,
    ParticipantOut,
    PriorityIn,
    PriorityOut,
    RatingIn,
    RatingOut,
    ReadOut,
    StatsOut,
    StatusIn,
    StatusOut,
    TagOut,
    TagsIn,
    TagsOut,
    TicketDetailOut,
    TicketPage,
    TypingIn,
    TypingOut,
    UnreadOut,
    UploadOut,
)
from apps.support.services import (
    DEFAULT_PAGE,
    InvalidRequest,
    MessageNotFound,
    NotPermitted,
    TicketNotFound,
    support_service,
)

try:  # pragma: no cover - exercised by whichever branch the project installs
    from infrastructure.auth.core.sessions import api_auth
except ImportError:  # pragma: no cover - only in a project without the auth apps
    from ninja.security import django_auth as api_auth  # type: ignore[assignment]

router = Router(auth=api_auth)


def _call(method: Any, *args: Any, **kwargs: Any) -> Any:
    """Run one service call, translating its refusals into HTTP.

    One translation, in one place. A missing thread and one belonging to
    somebody else are both a 404 -- saying which would confirm the existence of
    another person's complaint. A rule broken is a 403, because the row is one
    the caller can see and the refusal is about what they may do to it. A bad
    argument is a 400.
    """
    try:
        return method(*args, **kwargs)
    except (TicketNotFound, MessageNotFound) as missing:
        raise HttpError(404, str(missing)) from None
    except NotPermitted as refused:
        raise HttpError(403, str(refused)) from None
    except InvalidRequest as invalid:
        raise HttpError(400, str(invalid)) from None


# -- the queue and the client's own list ----------------------------------


@router.get("", response=TicketPage, summary="List the conversations this account can see")
def list_tickets(
    request: HttpRequest,
    status: Status | None = None,
    kind: Kind | None = None,
    priority: Priority | None = None,
    category: str | None = None,
    assignee: UUID | None = None,
    mine: bool = False,
    unassigned: bool = False,
    live: bool | None = None,
    breached: bool | None = None,
    search: str | None = None,
    limit: int = DEFAULT_PAGE,
    offset: int = 0,
) -> dict[str, Any]:
    """Most recently active first. A client sees the threads they opened; a
    member of staff sees the desk.

    `?live=true` narrows it to what still wants attention, which is the query a
    queue actually makes on open. `?mine=true` means "assigned to me" for an
    agent and "opened by me" for a client -- both are the right reading for
    whoever asked. `?unassigned=true` and `?assignee=` are staff-only.

    `?search=` matches the reference, the subject and what was said in the
    thread; for a client the message half is restricted to what they can read,
    so a term appearing only in an internal note does not reveal that the note
    exists.

    `total` counts everything matching the same filters, so a client can page
    without a second call. `?breached=` is applied after the page, because a
    breach is computed against the clock rather than stored -- so a filtered
    page can come back shorter than `limit`.
    """
    filters: dict[str, Any] = {
        "status": status,
        "kind": kind,
        "priority": priority,
        "category": category,
        "assignee": str(assignee) if assignee else None,
        "mine": mine,
        "unassigned": unassigned,
        "live": live,
        "search": search,
    }
    return {
        "tickets": _call(
            support_service.tickets,
            request.user,
            limit=limit,
            offset=offset,
            breached=breached,
            **filters,
        ),
        "total": _call(support_service.count, request.user, **filters),
        "limit": limit,
        "offset": offset,
    }


@router.post("", response={201: TicketDetailOut}, summary="Open a conversation")
def open_ticket(request: HttpRequest, payload: OpenIn) -> tuple[int, dict[str, Any]]:
    """Open a thread as its client, with the first message already in it.

    A `ticket` needs a subject and a body; a `chat` needs neither, because a
    widget opens the conversation before the visitor has typed anything.

    Opening one on somebody else's behalf is not possible here, deliberately: an
    agent creating a ticket "for" a client would be putting words in their mouth
    in a thread the client can read. The admin is where that belongs, where it
    is visibly an administrative act.
    """
    return 201, _call(
        support_service.open,
        request.user,
        subject=payload.subject,
        body=payload.body,
        kind=payload.kind,
        category=payload.category,
        priority=payload.priority or "",
        upload_ids=payload.upload_ids,
        data=payload.data,
    )


@router.get("/unread", response=UnreadOut, summary="Count what is waiting")
def unread(request: HttpRequest) -> dict[str, int]:
    """The badge: how many messages are unread, and across how many threads."""
    return support_service.unread(request.user)


@router.get("/categories", response=list[CategoryOut], summary="List what a ticket can be about")
def categories(request: HttpRequest) -> list[dict[str, Any]]:
    """The categories still open for filing, with the response times promised.

    The promises are published rather than kept internal: a client choosing
    where to file something is entitled to know what the desk has committed to.
    """
    return support_service.categories()


@router.get("/channels", response=list[ChannelOut], summary="List the open channels")
def channels(request: HttpRequest, search: str = "") -> list[dict[str, Any]]:
    """Every channel anybody signed in may join, whether or not you are in it.

    The only listing in this app that shows you something you are not already
    part of, because discovery is what a channel is for. Groups and private
    chats are deliberately absent: they are yours or they are invisible.
    """
    return _call(support_service.channels, request.user, search=search)


@router.post("/channels", response={201: TicketDetailOut}, summary="Open a channel")
def create_channel(request: HttpRequest, payload: ChannelIn) -> tuple[int, dict[str, Any]]:
    """Anybody signed in may open one. The address is refused if it is taken,
    never suffixed into uniqueness: `general-2` is a different channel from the
    one whoever asked meant."""
    return 201, _call(
        support_service.create_channel,
        request.user,
        payload.name,
        slug=payload.slug,
        body=payload.body,
    )


@router.post("/groups", response={201: TicketDetailOut}, summary="Open a private group")
def create_group(request: HttpRequest, payload: GroupIn) -> tuple[int, dict[str, Any]]:
    """Invisible to everybody but its members, staff included. Members are named
    now rather than invited later, because the first message should reach
    somebody and a group of one is not a group."""
    return 201, _call(
        support_service.create_group,
        request.user,
        payload.name,
        payload.members,
        body=payload.body,
    )


@router.post("/direct", response=TicketDetailOut, summary="Open a private chat")
def direct(request: HttpRequest, payload: DirectIn) -> dict[str, Any]:
    """The conversation between you and one other account, created only if it is
    new. A 200 rather than a 201 because the usual answer is the chat you
    already had -- calling this is how a client opens a DM, not how it counts
    them."""
    return _call(support_service.direct, request.user, payload.account)


@router.get("/tags", response=list[TagOut], summary="List the desk's tags")
def tags(request: HttpRequest) -> list[dict[str, Any]]:
    """Staff only. A tag is the desk's own note about a thread, not the client's."""
    return _call(support_service.tags, request.user)


@router.get(
    "/canned-replies", response=list[CannedReplyOut], summary="List the desk's saved replies"
)
def canned_replies(request: HttpRequest, category: str | None = None) -> list[dict[str, Any]]:
    """Staff only. Fetching one counts it, which is what tells a desk what to keep."""
    return _call(support_service.canned_replies, request.user, category=category)


@router.get("/stats", response=StatsOut, summary="The numbers the desk runs on")
def stats(request: HttpRequest) -> dict[str, Any]:
    """Staff only: counts by status, what is unassigned, what is in breach, and
    the mean satisfaction rating."""
    return _call(support_service.stats, request.user)


@router.post("/uploads", response={201: UploadOut}, summary="Send a file, before sending a message")
def stage_upload(
    request: HttpRequest,
    # `File(...)` in the default is how django-ninja declares a multipart body;
    # there is no other place to put it, so B008 is wrong here specifically.
    file: UploadedFile = File(...),  # noqa: B008
) -> tuple[int, dict[str, Any]]:
    """Store one file and answer with the id a message can claim it by.

    Two steps rather than one, because the two transports that matter cannot
    both do it in one: a WebSocket frame is JSON and cannot carry a multipart
    body. So the file comes here over HTTP and the message naming it goes over
    whichever transport the client is already holding open.

    An upload can be claimed exactly once, and only by the account that sent it.
    One nobody ever attaches is swept by `manage.py support_prune`.
    """
    try:
        return 201, support_service.stage_upload(request.user, file)
    except ValidationError as refused:
        raise HttpError(400, "; ".join(refused.messages)) from None


# -- one conversation ------------------------------------------------------


@router.get("/{ticket_id}", response=TicketDetailOut, summary="Read one conversation")
def get_ticket(request: HttpRequest, ticket_id: UUID) -> dict[str, Any]:
    """One thread, with this account's unread count and who else is in it.

    The participant list is trimmed for a client: they see the client and the
    agent who owns it, not a roster of everyone who has read their complaint.
    """
    return _call(support_service.ticket, request.user, ticket_id)


@router.get("/{ticket_id}/messages", response=MessagePage, summary="Read a conversation's messages")
def list_messages(
    request: HttpRequest, ticket_id: UUID, limit: int = DEFAULT_PAGE, offset: int = 0
) -> dict[str, Any]:
    """Oldest first, with the internal notes dropped for anybody who is not staff."""
    return _call(support_service.messages, request.user, ticket_id, limit=limit, offset=offset)


@router.post(
    "/{ticket_id}/messages", response={201: MessageOut}, summary="Say something in a conversation"
)
def send_message(
    request: HttpRequest, ticket_id: UUID, payload: MessageIn
) -> tuple[int, dict[str, Any]]:
    """Post a reply, and mark the thread read up to it.

    Sending is also reading: whatever was in the thread when you replied, you
    have seen. Doing that here rather than asking the client for a second call
    is what stops a badge surviving a conversation.

    A closed thread is refused rather than silently reopened -- `POST
    /{id}/reopen` is the explicit way, and it is available to the client.
    """
    return 201, _call(
        support_service.send,
        request.user,
        ticket_id,
        payload.body,
        upload_ids=payload.upload_ids,
        data=payload.data,
    )


@router.post("/{ticket_id}/notes", response={201: MessageOut}, summary="Leave a staff-only note")
def leave_note(
    request: HttpRequest, ticket_id: UUID, payload: MessageIn
) -> tuple[int, dict[str, Any]]:
    """Staff only. Lives in the same thread, in the order it was written, and is
    never served to the client."""
    return 201, _call(
        support_service.send,
        request.user,
        ticket_id,
        payload.body,
        upload_ids=payload.upload_ids,
        internal=True,
        data=payload.data,
    )


@router.post("/{ticket_id}/read", response=ReadOut, summary="Mark a conversation read")
def read(request: HttpRequest, ticket_id: UUID) -> dict[str, Any]:
    """Move this account's read watermark to now, and tell the other side.

    Never moves backwards, so a client firing this from two tabs cannot un-read
    its own progress.
    """
    return _call(support_service.read, request.user, ticket_id)


@router.post("/{ticket_id}/unread", response=ReadOut, summary="Mark a conversation unread")
def unread_ticket(request: HttpRequest, ticket_id: UUID) -> dict[str, Any]:
    """Drop the watermark entirely, putting the whole thread back in the badge."""
    return _call(support_service.unread_ticket, request.user, ticket_id)


@router.post("/{ticket_id}/status", response=StatusOut, summary="Move a conversation")
def set_status(request: HttpRequest, ticket_id: UUID, payload: StatusIn) -> dict[str, Any]:
    """Change the status, and write the change into the thread as an event.

    A client may settle or revive their own thread -- resolve, close, reopen.
    `pending` and `on_hold` are statements about what the desk is doing and are
    the desk's to make.
    """
    return _call(support_service.status, request.user, ticket_id, payload.status)


@router.post("/{ticket_id}/close", response=StatusOut, summary="Close a conversation")
def close(request: HttpRequest, ticket_id: UUID) -> dict[str, Any]:
    """Settle it for good. Closing is the one final state; `reopen` undoes it."""
    return _call(support_service.close, request.user, ticket_id)


@router.post("/{ticket_id}/reopen", response=StatusOut, summary="Reopen a conversation")
def reopen(request: HttpRequest, ticket_id: UUID) -> dict[str, Any]:
    """Revive a settled thread. The client's right of reply to being closed."""
    return _call(support_service.reopen, request.user, ticket_id)


@router.post("/{ticket_id}/assign", response=AssignOut, summary="Give a conversation to an agent")
def assign(request: HttpRequest, ticket_id: UUID, payload: AssignIn) -> dict[str, Any]:
    """Staff only. An empty `agent` returns it to the unassigned queue.

    Recorded as an internal event: who a complaint has been passed between is
    the desk's business, and telling the client answers a question they did not
    ask with something that reads as an apology.
    """
    return _call(support_service.assign, request.user, ticket_id, payload.agent)


@router.post("/{ticket_id}/claim", response=AssignOut, summary="Take a conversation yourself")
def claim(request: HttpRequest, ticket_id: UUID) -> dict[str, Any]:
    """Staff only, and refused when somebody else already has it -- two agents
    racing for the same ticket should both find out before either has typed an
    answer."""
    return _call(support_service.claim, request.user, ticket_id)


@router.post("/{ticket_id}/priority", response=PriorityOut, summary="Reprioritise a conversation")
def set_priority(request: HttpRequest, ticket_id: UUID, payload: PriorityIn) -> dict[str, Any]:
    """Staff only, and recorded internally."""
    return _call(support_service.priority, request.user, ticket_id, payload.priority)


@router.post("/{ticket_id}/tags", response=TagsOut, summary="Replace a conversation's tags")
def set_tags(request: HttpRequest, ticket_id: UUID, payload: TagsIn) -> dict[str, Any]:
    """Staff only. A replacement rather than an add, which is what a tag picker
    sends: the complete set it is now showing."""
    return _call(support_service.tag, request.user, ticket_id, payload.tags)


@router.post(
    "/{ticket_id}/participants",
    response={201: ParticipantOut},
    summary="Add somebody to a conversation",
)
def invite(request: HttpRequest, ticket_id: UUID, payload: InviteIn) -> tuple[int, dict[str, Any]]:
    """Staff only: a second agent, or the colleague of a client who is also
    affected. An observer who is not staff reads the public half, exactly as the
    client does."""
    return 201, _call(
        support_service.invite, request.user, ticket_id, payload.account, payload.role
    )


@router.post("/{ticket_id}/join", response={201: ParticipantOut}, summary="Join a channel")
def join_room(request: HttpRequest, ticket_id: UUID) -> tuple[int, dict[str, Any]]:
    """A channel only. A group and a private chat are joined by being put in one.

    Idempotent, so a client that does not track its own membership may call it
    before opening the room every time.
    """
    return 201, _call(support_service.join_room, request.user, ticket_id)


@router.post("/{ticket_id}/leave", response=LeftOut, summary="Leave a room")
def leave_room(request: HttpRequest, ticket_id: UUID) -> dict[str, Any]:
    """A channel or a group. A private chat cannot be left, only muted: half a
    private conversation is a state neither person can reason about."""
    return _call(support_service.leave_room, request.user, ticket_id)


@router.post("/{ticket_id}/rating", response=RatingOut, summary="Rate a settled conversation")
def rate(request: HttpRequest, ticket_id: UUID, payload: RatingIn) -> dict[str, Any]:
    """The client's, and only once the thread is resolved or closed.

    Not staff's: an agent rating their own handling of a ticket is a number that
    means nothing. Rating again overwrites, because an opinion formed the minute
    a ticket closed is allowed to change.
    """
    return _call(support_service.rate, request.user, ticket_id, payload.score, payload.comment)


@router.post("/{ticket_id}/typing", response=TypingOut, summary="Say you are typing")
def typing(request: HttpRequest, ticket_id: UUID, payload: TypingIn) -> dict[str, Any]:
    """Published to whoever is in the thread and never stored.

    Here for completeness rather than because anybody should use it: an
    indicator that is true for three seconds belongs on the socket, and a client
    posting one over HTTP has already lost the race it was trying to win.
    """
    return _call(support_service.typing, request.user, ticket_id, payload.typing)


# -- one message -----------------------------------------------------------


@router.patch("/messages/{message_id}", response=MessageOut, summary="Rewrite your own message")
def edit_message(request: HttpRequest, message_id: UUID, payload: EditIn) -> dict[str, Any]:
    """Only your own, and never once it is deleted.

    Staff get no exception. Editing what somebody else is recorded as having
    said is not moderation, and a desk that needs a message gone has `DELETE`,
    which leaves a tombstone saying so.
    """
    return _call(support_service.edit, request.user, message_id, payload.body)


@router.delete("/messages/{message_id}", response=MessageOut, summary="Retract a message")
def delete_message(request: HttpRequest, message_id: UUID) -> dict[str, Any]:
    """The author's, or staff's. Leaves a tombstone; never removes the row.

    Answered with the message rather than with nothing, because every reader has
    it on screen and has to be told what it became.
    """
    return _call(support_service.delete, request.user, message_id)
