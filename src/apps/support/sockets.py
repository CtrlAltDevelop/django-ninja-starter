"""The WebSocket: the conversation itself, live, for both sides of it.

Written against the ASGI interface directly rather than through Channels. The
whole consumer is one accept, two concurrent awaits and a dispatch table, and
Channels would bring a second routing layer, a second settings block and a
second definition of "a group" to hold it. Any ASGI server -- uvicorn, daphne,
hypercorn -- runs this as it stands. ``runserver`` does not, because Django's
development server is WSGI; the app's documentation says so, because the
alternative is discovering it as a connection that never opens.

**This socket admits nobody it cannot name.** The notification socket in this
project accepts anonymous connections because it has genuinely public traffic to
deliver. This one has none: every frame it sends belongs to a named conversation
-- a client and the desk, a private chat, a group, or a channel somebody joined.
So a handshake that carries no usable credential is **closed rather than
accepted**, and there is no signing in afterwards: no ``authenticate`` command,
and no signed-out state for a handler to worry about.

The credential comes from the handshake -- ``?token=``, a bearer subprotocol, an
``Authorization`` header, or a session cookie. See :mod:`apps.support.identity`.
A page that opens the socket before it has a token should open it after instead;
the failure is visible either way, because the upgrade is refused rather than
answered with a socket that never speaks.

**Connecting subscribes you to your own world.** The connection joins your
account channel, the desk channel if you are staff, and the channels of the
threads you are currently in -- bounded, newest first. From that moment one
socket carries every conversation you are part of, and a client renders a list
of threads and any one of them open without opening a second connection. A
thread you were not subscribed to -- an old one you have scrolled back to, or
one you were just added to -- is joined with ``subscribe``, which answers with
the tail of it so the client has something to render immediately.

**Confidentiality is applied on the way out.** An internal note is published to
the thread's channel like every other message and dropped here for a connection
whose account may not read it -- see :meth:`SupportSocket._is_for_us`. The
alternative, a second channel per thread, doubles every agent's subscriptions
and moves the rule somewhere nothing can test end to end.

Every frame in either direction is JSON with a ``type`` (server to client) or a
``command`` (client to server). Errors are frames, not closes: a mistyped ticket
id should cost one message, not the connection and the conversation flowing over
it. The one thing that does close the socket is the client going away.

**The socket can do everything the HTTP API can**, so a client that holds one
open needs no HTTP client beside it -- with one exception, and it is a protocol
limit rather than a choice: a file has to be uploaded over HTTP, because a
WebSocket frame is JSON and cannot carry a multipart body. The upload answers
with an id, and the message that carries it is sent over this socket like any
other. See :class:`apps.support.models.Upload`.

Titles on error frames are the same vocabulary the HTTP API answers with, so a
client translates one set of strings rather than two.
"""

import asyncio
import json
from typing import Any
from uuid import UUID

from asgiref.sync import sync_to_async
from django.conf import settings

from apps.support.broadcast import (
    Subscription,
    get_broker,
    staff_channel,
    ticket_channel,
    user_channel,
)
from apps.support.events import message_payload
from apps.support.identity import (
    Credentials,
    credentials_from_scope,
    user_from_credentials,
    user_summary,
)
from apps.support.models import LIVE_STATUSES, Message, Ticket, Visibility
from apps.support.services import (
    DEFAULT_PAGE,
    InvalidRequest,
    MessageNotFound,
    NotPermitted,
    TicketNotFound,
    support_service,
)

AUTHENTICATION_REQUIRED = "AUTHENTICATION_REQUIRED"
BAD_REQUEST = "BAD_REQUEST"
CONFLICT = "CONFLICT"
FORBIDDEN = "FORBIDDEN"
NOT_FOUND = "NOT_FOUND"
TOKEN_INVALID = "TOKEN_INVALID"

#: How many of an account's threads a connection is subscribed to on sign-in.
#: Bounded because an agent who has touched four thousand tickets would
#: otherwise open four thousand subscriptions to hear about the six that are
#: live. Anything outside the window is reachable with `subscribe`.
AUTO_SUBSCRIBE = 50

#: What an unauthenticated handshake is closed with. 1008 is the RFC 6455 code
#: for a policy violation, which is exactly what this is: the connection was
#: well-formed and is not allowed. Sent before any accept, so an ASGI server
#: answers the upgrade with an HTTP 403 and the browser reports a failure rather
#: than opening a socket that never speaks.
UNAUTHENTICATED_CLOSE = 1008


class SocketError(Exception):
    """A refusal the client should be told about without losing the connection."""

    def __init__(self, title: str, description: str) -> None:
        super().__init__(description)
        self.title = title
        self.description = description


def _identifier(value: Any, what: str = "ticket") -> UUID:
    """Read an id out of a frame, or refuse the frame."""
    try:
        return UUID(str(value))
    except (TypeError, ValueError):
        raise SocketError(BAD_REQUEST, f"That is not a {what} id.") from None


def _int(frame: dict[str, Any], name: str, default: int) -> int:
    """Read an integer argument, refusing anything that is not one.

    Coercing silently would mean ``{"limit": "all"}`` quietly returning the
    default page and the client never learning why it saw fifty rows.
    """
    value = frame.get(name)
    if value is None:
        return default
    if isinstance(value, bool) or not isinstance(value, int):
        raise SocketError(BAD_REQUEST, f"`{name}` has to be a whole number.")
    return value


def _bool(frame: dict[str, Any], name: str, default: bool = False) -> bool:
    value = frame.get(name)
    if value is None:
        return default
    if not isinstance(value, bool):
        raise SocketError(BAD_REQUEST, f"`{name}` has to be true or false.")
    return value


def _tristate(frame: dict[str, Any], name: str) -> bool | None:
    """A three-valued flag: absent means "either", which is not the same as false."""
    value = frame.get(name)
    if value is None:
        return None
    if not isinstance(value, bool):
        raise SocketError(BAD_REQUEST, f"`{name}` has to be true or false.")
    return value


def _text(frame: dict[str, Any], name: str) -> str:
    value = frame.get(name, "")
    if not isinstance(value, str):
        raise SocketError(BAD_REQUEST, f"`{name}` has to be a string.")
    return value


def _uploads(frame: dict[str, Any]) -> list[UUID]:
    """Read the upload ids a message is claiming."""
    raw = frame.get("upload_ids") or frame.get("uploads") or []
    if not isinstance(raw, list):
        raise SocketError(BAD_REQUEST, "`upload_ids` has to be a list of upload ids.")
    return [_identifier(item, "upload") for item in raw]


def _slugs(frame: dict[str, Any], name: str) -> list[str]:
    raw = frame.get(name) or []
    if not isinstance(raw, list) or any(not isinstance(item, str) for item in raw):
        raise SocketError(BAD_REQUEST, f"`{name}` has to be a list of strings.")
    return raw


def _data(frame: dict[str, Any]) -> dict[str, Any]:
    value = frame.get("data") or {}
    if not isinstance(value, dict):
        raise SocketError(BAD_REQUEST, "`data` has to be an object.")
    return value


def _translate(error: Exception) -> SocketError:
    """One service refusal as one error frame.

    The four service exceptions map onto the four titles the HTTP API answers
    with, so a client that has already learned what ``NOT_FOUND`` means from a
    REST call does not learn it again here.
    """
    if isinstance(error, TicketNotFound | MessageNotFound):
        return SocketError(NOT_FOUND, str(error))
    if isinstance(error, NotPermitted):
        return SocketError(FORBIDDEN, str(error))
    return SocketError(BAD_REQUEST, str(error))


def _live_ticket_ids(user: Any) -> list[Any]:
    """The threads a connection is subscribed to when it signs in.

    Live ones first and newest first, because those are the conversations
    somebody is actually having. A settled thread is still reachable -- the
    client subscribes to it when the person opens it -- and does not deserve a
    subscription held all day on the chance that they might.
    """
    return list(
        Ticket.objects.visible_to(user)
        .filter(status__in=LIVE_STATUSES)
        .ordered_for_queue()
        .values_list("id", flat=True)[:AUTO_SUBSCRIBE]
    )


def _tail(user: Any, ticket_id: UUID) -> dict[str, Any]:
    """The last few messages of a thread, oldest first, as this account may read them.

    What a client is handed the moment it joins a conversation, so that opening
    a thread renders immediately rather than after a round trip to the HTTP API.
    The rest of the history is what ``messages`` is for.
    """
    limit = settings.SUPPORT_SOCKET_BACKLOG
    ticket = support_service._ticket(user, ticket_id)
    if limit <= 0:
        return {"ticket": str(ticket.pk), "messages": []}
    recent = list(
        Message.objects.filter(ticket=ticket)
        .readable_by(user)
        .select_related("author")
        .prefetch_related("attachments")
        .order_by("-created_at", "-id")[:limit]
    )
    return {
        "ticket": str(ticket.pk),
        "messages": [message_payload(message) for message in reversed(recent)],
    }


class SupportSocket:
    """One connection: its subscriptions, who it belongs to, and the loop that serves it."""

    def __init__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        self._scope = scope
        self._asgi_receive = receive
        self._asgi_send = send
        self._subscription: Subscription | None = None
        self._user: Any | None = None
        #: Thread channels this connection has been authorised for. Kept here as
        #: well as in the subscription because a subscription can be added to
        #: but not removed from, and this is the set that says what the account
        #: currently signed in was actually allowed to join.
        self._joined: set[str] = set()

    # -- plumbing ---------------------------------------------------------

    async def _send_json(self, frame: dict[str, Any]) -> None:
        await self._asgi_send({"type": "websocket.send", "text": json.dumps(frame)})

    async def _send_error(self, title: str, description: str) -> None:
        await self._send_json({"type": "error", "title": title, "description": description})

    async def _join_channel(self, channel: str) -> None:
        """Start hearing one broker channel on this connection.

        Named apart from the ``join`` command, which is about joining a room and
        happens to call this.
        """
        assert self._subscription is not None
        await self._subscription.add(channel)

    async def _adopt(self, user: Any) -> None:
        """Attach an account to this connection and subscribe it to that account's world."""
        self._user = user
        await self._join_channel(user_channel(user.pk))
        if getattr(user, "is_staff", False):
            await self._join_channel(staff_channel())
        for ticket_id in await sync_to_async(_live_ticket_ids)(user):
            channel = ticket_channel(ticket_id)
            self._joined.add(channel)
            await self._join_channel(channel)

    # -- lifecycle --------------------------------------------------------

    async def run(self) -> None:
        """Authenticate, then serve. A connection that cannot say who it is gets neither.

        The handshake is refused rather than accepted-and-starved. Every frame
        this socket carries belongs to a named conversation, so there is nothing
        it could deliver to an anonymous connection; accepting one would buy a
        client the ability to hold a connection open and learn nothing from it,
        and would leave every command handler needing its own signed-out branch.

        Refusing is `websocket.close` before any accept, which is what an ASGI
        server turns into an HTTP 403 on the upgrade. A browser sees the
        connection fail rather than open and go quiet, which is the difference
        between a bug that is reported and a bug that is not.
        """
        message = await self._asgi_receive()
        if message["type"] != "websocket.connect":  # pragma: no cover - server contract
            return
        credentials = credentials_from_scope(self._scope)
        user = await sync_to_async(user_from_credentials)(credentials)
        if user is None:
            await self._asgi_send({"type": "websocket.close", "code": UNAUTHENTICATED_CLOSE})
            return
        await self._accept(credentials)
        self._subscription = get_broker().subscribe()
        try:
            await self._adopt(user)
            await self._send_json(
                {
                    "type": "ready",
                    "authenticated": True,
                    "user": user_summary(self._user),
                    "unread": await sync_to_async(support_service.unread)(self._user),
                }
            )
            await self._pump()
        finally:
            await self._subscription.close()

    async def _accept(self, credentials: Credentials) -> None:
        """Accept, echoing the subprotocol if one was offered.

        A browser that offered ``bearer`` and is answered with nothing closes the
        connection from its own side, so the echo is part of accepting rather
        than a nicety.
        """
        accept: dict[str, Any] = {"type": "websocket.accept"}
        if credentials.subprotocol:
            accept["subprotocol"] = credentials.subprotocol
        await self._asgi_send(accept)

    def _is_for_us(self, frame: dict[str, Any]) -> bool:
        """Whether a frame off the broker should reach this connection now.

        Three rules, and all three exist because a subscription can be added to
        but not removed from -- one task owning the connection is what makes
        concurrent ``add`` safe, so nothing is ever unsubscribed and everything
        is filtered here instead.

        1. Signed out: nothing goes out. A shared browser that signed out is
           still joined to that account's channels underneath.
        2. An internal note reaches staff only. This is the app's one real
           confidentiality rule, and this is where it is enforced for everybody
           who is connected.
        3. A thread this connection has left with ``unsubscribe`` is dropped,
           so a client that closed a conversation stops being sent it.
        """
        if self._user is None:
            return False
        message = frame.get("message")
        if (
            isinstance(message, dict)
            and message.get("visibility") == Visibility.INTERNAL
            and not getattr(self._user, "is_staff", False)
        ):
            return False
        ticket_id = frame.get("ticket")
        per_thread = {"message", "typing", "read", "presence"}
        if isinstance(ticket_id, str) and frame.get("type") in per_thread:
            return ticket_channel(ticket_id) in self._joined
        return True

    async def _pump(self) -> None:
        """Serve whichever comes first: a frame from the client, or one from the broker.

        Two standing tasks rather than a poll of each in turn, so a reply posted
        while the connection is idle goes out immediately instead of waiting for
        the client to say something.
        """
        assert self._subscription is not None
        from_client = asyncio.ensure_future(self._asgi_receive())
        from_broker = asyncio.ensure_future(self._subscription.get())
        try:
            while True:
                done, _ = await asyncio.wait(
                    {from_client, from_broker}, return_when=asyncio.FIRST_COMPLETED
                )
                if from_broker in done:
                    frame = from_broker.result()
                    if self._is_for_us(frame):
                        await self._send_json(frame)
                    from_broker = asyncio.ensure_future(self._subscription.get())
                if from_client in done:
                    message = from_client.result()
                    if message["type"] == "websocket.disconnect":
                        await self._on_disconnect()
                        return
                    await self._handle(message)
                    from_client = asyncio.ensure_future(self._asgi_receive())
        finally:
            for task in (from_client, from_broker):
                task.cancel()

    async def _on_disconnect(self) -> None:
        """Tell every thread this connection had open that somebody has gone.

        Presence is the one thing a client cannot report for itself: a browser
        that was closed, crashed or lost its network sends nothing. So the
        server says it on the connection's behalf, which is the only place that
        can.
        """
        if self._user is None:
            return
        for channel in list(self._joined):
            ticket_id = channel.rsplit(":", 1)[-1]
            try:
                await sync_to_async(support_service.presence)(
                    self._user, UUID(ticket_id), present=False
                )
            except Exception:  # pragma: no cover - a disconnect must never raise
                continue

    # -- commands ---------------------------------------------------------

    async def _handle(self, message: dict[str, Any]) -> None:
        text = message.get("text")
        if text is None:
            await self._send_error(BAD_REQUEST, "Send text frames, not binary ones.")
            return
        try:
            frame = json.loads(text)
        except json.JSONDecodeError:
            await self._send_error(BAD_REQUEST, "That was not JSON.")
            return
        if not isinstance(frame, dict):
            await self._send_error(BAD_REQUEST, "A command has to be a JSON object.")
            return

        command = str(frame.get("command", ""))
        handler = self._handler(command)
        if handler is None:
            await self._send_error(BAD_REQUEST, "Unknown command.")
            return
        try:
            await handler(frame)
        except SocketError as error:
            await self._send_error(error.title, error.description)
        except (TicketNotFound, MessageNotFound, NotPermitted, InvalidRequest) as error:
            translated = _translate(error)
            await self._send_error(translated.title, translated.description)

    #: Every command this socket accepts, in the order the documentation lists
    #: them: the four that answer with no account at all, then reading, then
    #: talking, then the desk's own. One tuple, so :meth:`commands` cannot fall
    #: behind the dispatch table -- the handler for each is ``_<name>``.
    COMMANDS = (
        # Open to anybody who got in, which is everybody: the handshake
        # already refused the connections that could not say who they were.
        "ping",
        "whoami",
        # Reading
        "tickets",
        "ticket",
        "messages",
        "unread",
        "categories",
        "subscribe",
        "unsubscribe",
        # Talking
        "open",
        "send",
        "note",
        "edit",
        "delete",
        "read",
        "unread_ticket",
        "typing",
        "presence",
        # Either side
        "status",
        "close",
        "reopen",
        "rate",
        # Rooms: channels, groups and private chats
        "channels",
        "create_channel",
        "create_group",
        "direct",
        "join",
        "leave",
        # The desk's own
        "assign",
        "claim",
        "priority",
        "tag",
        "invite",
        "tags",
        "canned",
        "stats",
    )

    def _handler(self, command: str) -> Any:
        """The method serving one command, or ``None`` if there is no such command."""
        if command not in self.COMMANDS:
            return None
        return getattr(self, f"_{command}")

    @classmethod
    def commands(cls) -> tuple[str, ...]:
        """Every command this socket accepts, for documentation to be checked against."""
        return cls.COMMANDS

    def _require_user(self) -> Any:
        """The account this connection belongs to, which is never ``None``.

        The handshake refuses an unauthenticated connection, so by the time any
        handler runs there is an account. Kept as an assertion rather than
        inlined so that a future command cannot quietly start serving nobody,
        and so the handlers read the same as they did when it could fail.
        """
        assert self._user is not None, "the handshake admits nobody without an account"
        return self._user

    # -- open to anybody --------------------------------------------------

    async def _ping(self, frame: dict[str, Any]) -> None:
        """Answered so a client can keep an idle connection alive through a proxy."""
        await self._send_json({"type": "pong"})

    async def _whoami(self, frame: dict[str, Any]) -> None:
        """Who this connection currently belongs to, if anybody.

        Answered rather than refused when nobody is signed in: "you are nobody"
        is the useful reply to that question, and a client reconnecting after a
        sleep asks it precisely because it does not know.
        """
        await self._send_json(
            {
                "type": "whoami",
                "authenticated": self._user is not None,
                "user": user_summary(self._user) if self._user else None,
                "unread": await sync_to_async(support_service.unread)(self._user)
                if self._user
                else {"messages": 0, "tickets": 0},
            }
        )

    # -- reading ----------------------------------------------------------

    def _filters(self, frame: dict[str, Any]) -> dict[str, Any]:
        """The queue filters, read the same way for ``tickets`` and its count."""
        return {
            "status": _text(frame, "status") or None,
            "kind": _text(frame, "kind") or None,
            "priority": _text(frame, "priority") or None,
            "category": _text(frame, "category") or None,
            "assignee": _text(frame, "assignee") or None,
            "mine": _bool(frame, "mine"),
            "unassigned": _bool(frame, "unassigned"),
            "live": _tristate(frame, "live"),
            "search": _text(frame, "search") or None,
        }

    async def _tickets(self, frame: dict[str, Any]) -> None:
        """A page of threads -- a client's own, or the desk's queue.

        The same arguments the endpoint takes, under the same names, and the
        reply carries the unfiltered total so a client can page without a second
        call.
        """
        user = self._require_user()
        filters = self._filters(frame)
        limit = _int(frame, "limit", DEFAULT_PAGE)
        offset = _int(frame, "offset", 0)
        rows = await sync_to_async(support_service.tickets)(
            user, limit=limit, offset=offset, breached=_tristate(frame, "breached"), **filters
        )
        total = await sync_to_async(support_service.count)(user, **filters)
        await self._send_json(
            {"type": "tickets", "tickets": rows, "total": total, "limit": limit, "offset": offset}
        )

    async def _ticket(self, frame: dict[str, Any]) -> None:
        """One thread, with this account's unread count and who else is in it."""
        user = self._require_user()
        ticket = await sync_to_async(support_service.ticket)(
            user, _identifier(frame.get("ticket") or frame.get("id"))
        )
        await self._send_json({"type": "ticket", "ticket": ticket, "reason": "requested"})

    async def _messages(self, frame: dict[str, Any]) -> None:
        """A page of one thread, oldest first, with the notes dropped for a client."""
        user = self._require_user()
        page = await sync_to_async(support_service.messages)(
            user,
            _identifier(frame.get("ticket") or frame.get("id")),
            limit=_int(frame, "limit", DEFAULT_PAGE),
            offset=_int(frame, "offset", 0),
        )
        await self._send_json({"type": "messages", **page})

    async def _unread(self, frame: dict[str, Any]) -> None:
        """The badge: how many messages are waiting, and in how many threads."""
        user = self._require_user()
        await self._send_json(
            {"type": "unread", **await sync_to_async(support_service.unread)(user)}
        )

    async def _categories(self, frame: dict[str, Any]) -> None:
        self._require_user()
        await self._send_json(
            {
                "type": "categories",
                "categories": await sync_to_async(support_service.categories)(),
            }
        )

    async def _subscribe(self, frame: dict[str, Any]) -> None:
        """Join a thread's channel, and get its tail to render immediately.

        Authorisation happens here rather than at delivery: the service resolves
        the thread against what this account may see, so a channel is only ever
        joined for a conversation the account is in. That is what makes the
        broker's fan-out safe -- nothing filters by ticket on the way out except
        the unsubscribe check.
        """
        user = self._require_user()
        ticket_id = _identifier(frame.get("ticket") or frame.get("id"))
        tail = await sync_to_async(_tail)(user, ticket_id)
        channel = ticket_channel(ticket_id)
        self._joined.add(channel)
        await self._join_channel(channel)
        await self._send_json({"type": "subscribed", **tail})
        # Presence after the tail, so the other side learns somebody arrived
        # only once this connection can actually render what they say next.
        await sync_to_async(support_service.presence)(user, ticket_id, present=True)

    async def _unsubscribe(self, frame: dict[str, Any]) -> None:
        """Stop being sent a thread. The channel stays joined; the filter drops it."""
        user = self._require_user()
        ticket_id = _identifier(frame.get("ticket") or frame.get("id"))
        self._joined.discard(ticket_channel(ticket_id))
        await self._send_json({"type": "unsubscribed", "ticket": str(ticket_id)})
        await sync_to_async(support_service.presence)(user, ticket_id, present=False)

    # -- talking ----------------------------------------------------------

    async def _open(self, frame: dict[str, Any]) -> None:
        """Open a thread and be subscribed to it in the same round trip.

        The subscription is the point of doing this over the socket: a client
        that opened a chat and then had to subscribe would miss whatever the
        desk said in between.
        """
        user = self._require_user()
        ticket = await sync_to_async(support_service.open)(
            user,
            subject=_text(frame, "subject"),
            body=_text(frame, "body"),
            kind=_text(frame, "kind") or "chat",
            category=_text(frame, "category") or None,
            priority=_text(frame, "priority"),
            upload_ids=_uploads(frame),
            data=_data(frame),
        )
        channel = ticket_channel(ticket["id"])
        self._joined.add(channel)
        await self._join_channel(channel)
        await self._send_json({"type": "opened", "ticket": ticket})

    async def _send_message(self, frame: dict[str, Any], *, internal: bool) -> dict[str, Any]:
        user = self._require_user()
        ticket_id = _identifier(frame.get("ticket") or frame.get("id"))
        message = await sync_to_async(support_service.send)(
            user,
            ticket_id,
            _text(frame, "body"),
            upload_ids=_uploads(frame),
            internal=internal,
            data=_data(frame),
        )
        # Sending into a thread this connection had not joined subscribes it, so
        # an agent answering out of a queue starts hearing the reply without a
        # second command.
        channel = ticket_channel(ticket_id)
        self._joined.add(channel)
        await self._join_channel(channel)
        return message

    async def _send(self, frame: dict[str, Any]) -> None:
        """Say something in a thread. The command this whole app exists for."""
        message = await self._send_message(frame, internal=False)
        await self._send_json({"type": "sent", "message": message})

    async def _note(self, frame: dict[str, Any]) -> None:
        """Leave a staff-only note in a thread. Refused for anybody else."""
        message = await self._send_message(frame, internal=True)
        await self._send_json({"type": "sent", "message": message})

    async def _edit(self, frame: dict[str, Any]) -> None:
        user = self._require_user()
        message = await sync_to_async(support_service.edit)(
            user,
            _identifier(frame.get("message") or frame.get("id"), "message"),
            _text(frame, "body"),
        )
        await self._send_json({"type": "edited", "message": message})

    async def _delete(self, frame: dict[str, Any]) -> None:
        user = self._require_user()
        message = await sync_to_async(support_service.delete)(
            user, _identifier(frame.get("message") or frame.get("id"), "message")
        )
        await self._send_json({"type": "deleted", "message": message})

    async def _read(self, frame: dict[str, Any]) -> None:
        user = self._require_user()
        result = await sync_to_async(support_service.read)(
            user, _identifier(frame.get("ticket") or frame.get("id"))
        )
        await self._send_json({"type": "read", **result})

    async def _unread_ticket(self, frame: dict[str, Any]) -> None:
        """Put a whole thread back in the badge. Named apart from ``unread``,
        which is the badge itself."""
        user = self._require_user()
        result = await sync_to_async(support_service.unread_ticket)(
            user, _identifier(frame.get("ticket") or frame.get("id"))
        )
        await self._send_json({"type": "unread_ticket", **result})

    async def _typing(self, frame: dict[str, Any]) -> None:
        """Say you are typing. Not stored, and not answered with a reply frame --
        the indicator itself arrives on the thread channel like anybody else's."""
        user = self._require_user()
        result = await sync_to_async(support_service.typing)(
            user,
            _identifier(frame.get("ticket") or frame.get("id")),
            typing=_bool(frame, "typing", True),
        )
        await self._send_json({"type": "typing_ack", **result})

    async def _presence(self, frame: dict[str, Any]) -> None:
        user = self._require_user()
        result = await sync_to_async(support_service.presence)(
            user,
            _identifier(frame.get("ticket") or frame.get("id")),
            present=_bool(frame, "present", True),
        )
        await self._send_json({"type": "presence_ack", **result})

    # -- either side ------------------------------------------------------

    async def _status(self, frame: dict[str, Any]) -> None:
        user = self._require_user()
        result = await sync_to_async(support_service.status)(
            user, _identifier(frame.get("ticket") or frame.get("id")), _text(frame, "status")
        )
        await self._send_json({"type": "status", **result})

    async def _close(self, frame: dict[str, Any]) -> None:
        user = self._require_user()
        result = await sync_to_async(support_service.close)(
            user, _identifier(frame.get("ticket") or frame.get("id"))
        )
        await self._send_json({"type": "status", **result})

    async def _reopen(self, frame: dict[str, Any]) -> None:
        user = self._require_user()
        result = await sync_to_async(support_service.reopen)(
            user, _identifier(frame.get("ticket") or frame.get("id"))
        )
        await self._send_json({"type": "status", **result})

    async def _rate(self, frame: dict[str, Any]) -> None:
        user = self._require_user()
        score = frame.get("score")
        if isinstance(score, bool) or not isinstance(score, int):
            raise SocketError(BAD_REQUEST, "`score` has to be a whole number of stars.")
        result = await sync_to_async(support_service.rate)(
            user,
            _identifier(frame.get("ticket") or frame.get("id")),
            score,
            _text(frame, "comment"),
        )
        await self._send_json({"type": "rated", **result})

    # -- rooms ------------------------------------------------------------

    async def _channels(self, frame: dict[str, Any]) -> None:
        """Every open channel, joined or not. The only discovery this socket does."""
        user = self._require_user()
        found = await sync_to_async(support_service.channels)(user, search=_text(frame, "search"))
        await self._send_json({"type": "channels", "channels": found})

    async def _create_channel(self, frame: dict[str, Any]) -> None:
        user = self._require_user()
        room = await sync_to_async(support_service.create_channel)(
            user, _text(frame, "name"), slug=_text(frame, "slug"), body=_text(frame, "body")
        )
        await self._subscribe_to(room["id"])
        await self._send_json({"type": "created", "ticket": room})

    async def _create_group(self, frame: dict[str, Any]) -> None:
        user = self._require_user()
        members = [_identifier(item, "account") for item in _slugs(frame, "members")]
        room = await sync_to_async(support_service.create_group)(
            user, _text(frame, "name"), members, body=_text(frame, "body")
        )
        await self._subscribe_to(room["id"])
        await self._send_json({"type": "created", "ticket": room})

    async def _direct(self, frame: dict[str, Any]) -> None:
        """The private chat with one other account, opening it only if it is new."""
        user = self._require_user()
        room = await sync_to_async(support_service.direct)(
            user, _identifier(frame.get("account"), "account")
        )
        await self._subscribe_to(room["id"])
        await self._send_json({"type": "created", "ticket": room})

    async def _join(self, frame: dict[str, Any]) -> None:
        """Join a channel, and start hearing it on this connection in the same round trip."""
        user = self._require_user()
        ticket_id = _identifier(frame.get("ticket") or frame.get("id"))
        participant = await sync_to_async(support_service.join_room)(user, ticket_id)
        await self._subscribe_to(str(ticket_id))
        await self._send_json({"type": "joined", "participant": participant})

    async def _leave(self, frame: dict[str, Any]) -> None:
        user = self._require_user()
        ticket_id = _identifier(frame.get("ticket") or frame.get("id"))
        result = await sync_to_async(support_service.leave_room)(user, ticket_id)
        self._joined.discard(ticket_channel(ticket_id))
        await self._send_json({"type": "left", **result})

    async def _subscribe_to(self, ticket_id: str) -> None:
        """Start hearing a room on this connection.

        Called by every command that puts this account into one, so that opening
        or joining is a single round trip: a client that had to subscribe
        afterwards would miss whatever was said in between.
        """
        channel = ticket_channel(ticket_id)
        self._joined.add(channel)
        await self._join_channel(channel)

    # -- the desk's own ---------------------------------------------------

    async def _assign(self, frame: dict[str, Any]) -> None:
        user = self._require_user()
        agent = frame.get("agent")
        result = await sync_to_async(support_service.assign)(
            user,
            _identifier(frame.get("ticket") or frame.get("id")),
            _identifier(agent, "account") if agent else None,
        )
        await self._send_json({"type": "assigned", **result})

    async def _claim(self, frame: dict[str, Any]) -> None:
        user = self._require_user()
        result = await sync_to_async(support_service.claim)(
            user, _identifier(frame.get("ticket") or frame.get("id"))
        )
        await self._send_json({"type": "assigned", **result})

    async def _priority(self, frame: dict[str, Any]) -> None:
        user = self._require_user()
        result = await sync_to_async(support_service.priority)(
            user, _identifier(frame.get("ticket") or frame.get("id")), _text(frame, "priority")
        )
        await self._send_json({"type": "priority", **result})

    async def _tag(self, frame: dict[str, Any]) -> None:
        user = self._require_user()
        result = await sync_to_async(support_service.tag)(
            user, _identifier(frame.get("ticket") or frame.get("id")), _slugs(frame, "tags")
        )
        await self._send_json({"type": "tagged", **result})

    async def _invite(self, frame: dict[str, Any]) -> None:
        user = self._require_user()
        participant = await sync_to_async(support_service.invite)(
            user,
            _identifier(frame.get("ticket") or frame.get("id")),
            _identifier(frame.get("account"), "account"),
            _text(frame, "role") or "observer",
        )
        await self._send_json({"type": "invited", "participant": participant})

    async def _tags(self, frame: dict[str, Any]) -> None:
        user = self._require_user()
        await self._send_json(
            {"type": "tags", "tags": await sync_to_async(support_service.tags)(user)}
        )

    async def _canned(self, frame: dict[str, Any]) -> None:
        """The desk's saved replies. Staff only."""
        user = self._require_user()
        replies = await sync_to_async(support_service.canned_replies)(
            user, category=_text(frame, "category") or None
        )
        await self._send_json({"type": "canned", "replies": replies})

    async def _stats(self, frame: dict[str, Any]) -> None:
        """The numbers a desk runs on. Staff only."""
        user = self._require_user()
        await self._send_json(
            {"type": "stats", "stats": await sync_to_async(support_service.stats)(user)}
        )


async def support_socket(scope: dict[str, Any], receive: Any, send: Any) -> None:
    """The ASGI application this app publishes. Mounted by :mod:`config.sockets`."""
    await SupportSocket(scope, receive, send).run()
