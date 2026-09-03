"""The WebSocket: a public feed you can join, and a private one you can unlock.

Written against the ASGI interface directly rather than through Channels. The
whole consumer is one accept, two concurrent awaits and a dispatch table, and
Channels would bring a second routing layer, a second settings block and a
second definition of "a group" to hold it. Any ASGI server -- uvicorn, daphne,
hypercorn -- runs this as it stands. ``runserver`` does not, because Django's
development server is WSGI; the app's documentation says so, because the
alternative is discovering it as a connection that never opens.

**The connection is useful before it is authenticated.** That is the design.
Anyone who connects joins the global channel immediately and starts receiving
what was addressed to everybody -- a status banner, a maintenance notice -- with
no credential at all. Sending ``{"command": "authenticate", "token": ...}`` then
adds that account's own channel to the same connection, and from that moment it
carries both. A client therefore opens one socket, not one per audience, and a
page that renders announcements to signed-out visitors needs no special case.

Credentials in the handshake -- ``?token=``, a bearer subprotocol, an
``Authorization`` header, a session cookie -- are honoured too, so a client that
knows who it is at connect time does not have to wait a round trip. See
:mod:`apps.notifications.identity`.

Every frame in either direction is JSON with a ``type`` (server to client) or a
``command`` (client to server). Errors are frames, not closes: a mistyped
notification id should cost one message, not the connection and everything else
that was flowing over it. The one thing that does close the socket is the client
going away.

**The socket can do everything the HTTP API can**, so a client that holds one
open needs no HTTP client beside it: ``list``, ``get`` and ``count`` read the
history the socket itself never pushes, and ``read``, ``unread_one``,
``read_all``, ``dismiss``, ``restore`` and ``dismiss_all`` change state. Four
commands work with no account at all -- ``ping``, ``authenticate``, ``whoami``
and ``deauthenticate`` -- and the rest answer ``AUTHENTICATION_REQUIRED`` until
one is presented. Every one of them, including the four, accepts a ``token``
that signs the connection in before it runs.

State changes are also pushed to the account's *other* connections as a ``state``
frame, so a badge cleared on a phone clears on the laptop.

Titles on error frames are the same vocabulary the HTTP API answers with, so a
client translates one set of strings rather than two.
"""

import asyncio
import json
from typing import Any
from uuid import UUID

from asgiref.sync import sync_to_async
from django.conf import settings

from apps.notifications.broadcast import Subscription, get_broker, global_channel, user_channel
from apps.notifications.events import payload
from apps.notifications.identity import (
    Credentials,
    credentials_from_scope,
    user_from_credentials,
    user_from_token,
    user_summary,
)
from apps.notifications.models import Audience, Notification, unread_count
from apps.notifications.services import (
    DEFAULT_PAGE,
    NotificationNotFound,
    notification_service,
)

AUTHENTICATION_REQUIRED = "AUTHENTICATION_REQUIRED"
BAD_REQUEST = "BAD_REQUEST"
CONFLICT = "CONFLICT"
NOT_FOUND = "NOT_FOUND"
TOKEN_INVALID = "TOKEN_INVALID"


class SocketError(Exception):
    """A refusal the client should be told about without losing the connection."""

    def __init__(self, title: str, description: str) -> None:
        super().__init__(description)
        self.title = title
        self.description = description


def _backlog(user: Any) -> list[dict[str, Any]]:
    """The unread notifications a client should be caught up with, oldest first.

    Bounded, because "everything you missed" is unbounded and a client that has
    been away for a year should not be sent a year on connect. The HTTP list
    endpoint -- and the socket's own ``list`` command -- is where the rest lives.
    """
    limit = settings.NOTIFICATIONS_SOCKET_BACKLOG
    if limit <= 0:
        return []
    recent = list(Notification.objects.for_user(user).unread()[:limit])
    return [payload(notification) for notification in reversed(recent)]


def _identifier(value: Any) -> UUID:
    """Read a notification id out of a frame, or refuse the frame."""
    try:
        return UUID(str(value))
    except (TypeError, ValueError):
        raise SocketError(BAD_REQUEST, "That is not a notification id.") from None


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


def _tristate(frame: dict[str, Any], name: str) -> bool | None:
    """A three-valued flag: absent means "either", which is not the same as false."""
    value = frame.get(name)
    if value is None:
        return None
    if not isinstance(value, bool):
        raise SocketError(BAD_REQUEST, f"`{name}` has to be true or false.")
    return value


def _one(user: Any, frame: dict[str, Any], method: str) -> dict[str, Any]:
    """Run one of the service's single-notification changes, translating its refusal."""
    identifier = _identifier(frame.get("id"))
    try:
        return getattr(notification_service, method)(user, identifier)
    except NotificationNotFound as missing:
        raise SocketError(NOT_FOUND, str(missing)) from None


class NotificationSocket:
    """One connection: its subscription, who it belongs to, and the loop that serves it."""

    def __init__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        self._scope = scope
        self._receive = receive
        self._send = send
        self._subscription: Subscription | None = None
        self._user: Any | None = None

    # -- plumbing ---------------------------------------------------------

    async def _send_json(self, frame: dict[str, Any]) -> None:
        await self._send({"type": "websocket.send", "text": json.dumps(frame)})

    async def _send_error(self, title: str, description: str) -> None:
        await self._send_json({"type": "error", "title": title, "description": description})

    async def _adopt(self, user: Any) -> None:
        """Attach an account to this connection and start delivering its private channel."""
        self._user = user
        assert self._subscription is not None
        await self._subscription.add(user_channel(user.pk))

    async def _catch_up(self) -> None:
        for notification in await sync_to_async(_backlog)(self._user):
            await self._send_json({"type": "notification", "notification": notification})

    # -- lifecycle --------------------------------------------------------

    async def run(self) -> None:
        message = await self._receive()
        if message["type"] != "websocket.connect":  # pragma: no cover - server contract
            return
        credentials = credentials_from_scope(self._scope)
        await self._accept(credentials)
        self._subscription = get_broker().subscribe()
        try:
            await self._subscription.add(global_channel())
            user = await sync_to_async(user_from_credentials)(credentials)
            if user is not None:
                await self._adopt(user)
            await self._send_json(
                {
                    "type": "ready",
                    "authenticated": self._user is not None,
                    "user": user_summary(self._user) if self._user else None,
                    "unread": await sync_to_async(unread_count)(self._user) if self._user else 0,
                }
            )
            if self._user is not None:
                await self._catch_up()
            await self._pump()
        finally:
            await self._subscription.close()

    def _is_for_us(self, frame: dict[str, Any]) -> bool:
        """Whether a frame off the broker should reach this connection now.

        A subscription can be added to but not removed from -- one task owning
        the connection is what makes concurrent `add` safe -- so a connection
        that has signed out is still joined to that account's channel. Rather
        than teach the broker to leave one, everything private is filtered here:
        while nobody is signed in, only what was addressed to everybody goes out.
        """
        if self._user is not None:
            return True
        notification = frame.get("notification")
        return isinstance(notification, dict) and notification.get("audience") == Audience.GLOBAL

    async def _accept(self, credentials: Credentials) -> None:
        """Accept, echoing the subprotocol if one was offered.

        A browser that offered ``bearer`` and is answered with nothing closes the
        connection from its own side, so the echo is part of accepting rather
        than a nicety.
        """
        accept: dict[str, Any] = {"type": "websocket.accept"}
        if credentials.subprotocol:
            accept["subprotocol"] = credentials.subprotocol
        await self._send(accept)

    async def _pump(self) -> None:
        """Serve whichever comes first: a frame from the client, or one from the broker.

        Two standing tasks rather than a poll of each in turn, so a notification
        published while the connection is idle goes out immediately instead of
        waiting for the client to say something.
        """
        assert self._subscription is not None
        from_client = asyncio.ensure_future(self._receive())
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
                        return
                    await self._handle(message)
                    from_client = asyncio.ensure_future(self._receive())
        finally:
            for task in (from_client, from_broker):
                task.cancel()

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
            # Before the command, so a token that will not do refuses the whole
            # frame rather than letting the command run as somebody else -- or,
            # worse, as nobody. Compared by name rather than against the bound
            # method, which is a new object on every attribute access and would
            # never match itself.
            if command != "authenticate":
                await self._sign_in_if_offered(frame)
            await handler(frame)
        except SocketError as error:
            await self._send_error(error.title, error.description)

    #: Every command this socket accepts, in the order the documentation lists
    #: them: the four that answer with no account at all, then the rest, which
    #: are refused with ``AUTHENTICATION_REQUIRED`` until a credential arrives.
    #: One tuple, so :meth:`commands` cannot fall behind the dispatch table --
    #: the handler for each is ``_<name>``.
    COMMANDS = (
        "ping",
        "authenticate",
        "whoami",
        "deauthenticate",
        "list",
        "get",
        "count",
        "unread",
        "read",
        "unread_one",
        "read_all",
        "dismiss",
        "restore",
        "dismiss_all",
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
        if self._user is None:
            raise SocketError(
                AUTHENTICATION_REQUIRED,
                "Send an authenticate command before reading your own notifications.",
            )
        return self._user

    async def _account_for(self, token: str) -> Any:
        """The account a token names, or a refusal the client can be told about.

        Shared by the ``authenticate`` command and by a token riding on any other
        command, so the two refuse in the same words for the same reasons: a
        token that names nobody, and a token that names somebody other than
        whoever this connection already belongs to. The second is a conflict
        rather than a switch, because the connection is already joined to the
        first account's channel and there is no honest way to serve two people
        down one socket.
        """
        user = await sync_to_async(user_from_token)(token)
        if user is None:
            raise SocketError(TOKEN_INVALID, "That token does not identify anybody.")
        if self._user is not None and self._user.pk != user.pk:
            raise SocketError(
                CONFLICT, "This connection is already signed in. Open a new one instead."
            )
        return user

    async def _welcome(self, user: Any) -> None:
        """Adopt an account, say so, and hand over what it missed while away."""
        await self._adopt(user)
        await self._send_json(
            {
                "type": "authenticated",
                "user": user_summary(user),
                "unread": await sync_to_async(unread_count)(user),
            }
        )
        await self._catch_up()

    async def _sign_in_if_offered(self, frame: dict[str, Any]) -> None:
        """Honour a ``token`` carried by a command that is not ``authenticate``.

        Optional, so a connection that authenticated at the handshake or in an
        earlier frame goes on sending bare commands. Signing in this way is
        indistinguishable from having sent ``authenticate`` first -- the client
        gets the same ``authenticated`` frame and the same backlog ahead of its
        command's own reply -- so a client has one set of frames to handle
        however it chose to present its credential.

        Already signed in as the same account: nothing to announce, and the
        client did not ask for an acknowledgement. A different account still
        conflicts, because piggybacking a token is a shorter way to authenticate
        and not a way around what authenticating refuses.
        """
        token = str(frame.get("token") or "").strip()
        if not token:
            return
        user = await self._account_for(token)
        if self._user is None:
            await self._welcome(user)

    async def _authenticate(self, frame: dict[str, Any]) -> None:
        """Prove who you are, and start receiving what was addressed to you.

        Authenticating twice as the same account changes nothing, but is still
        answered -- a client that refreshed its token should neither have to
        reconnect nor be left waiting for a reply that never comes.
        """
        await self._welcome(await self._account_for(str(frame.get("token", ""))))

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
                "unread": await sync_to_async(unread_count)(self._user) if self._user else 0,
            }
        )

    async def _deauthenticate(self, frame: dict[str, Any]) -> None:
        """Forget the account, without dropping the connection or the public feed.

        A shared browser signing out should stop receiving one person's private
        traffic immediately, and should go on receiving the announcements a
        signed-out visitor gets. Only the socket's own view of the account is
        dropped; the credential itself is the auth app's to revoke.

        The private channel stays subscribed underneath -- a subscription can be
        added to but not removed from, and adding removal would mean a second
        Redis round trip on a connection whose whole safety comes from one task
        owning it. So the filter is here: frames for an account this connection
        no longer claims are dropped before they are sent. Signing out and
        straight back in as the same account therefore costs nothing.
        """
        was = self._user
        self._user = None
        await self._send_json(
            {"type": "deauthenticated", "user": user_summary(was) if was else None}
        )

    async def _list(self, frame: dict[str, Any]) -> None:
        """The history the socket itself never pushes -- the same page HTTP returns.

        Here so that a client which has a socket open does not need an HTTP
        client as well to render its tray. The arguments are the endpoint's,
        under the same names, and the reply carries the unfiltered total so a
        client can page without a second call.
        """
        user = self._require_user()
        arguments: dict[str, Any] = {
            "unread": _tristate(frame, "unread"),
            "level": str(frame["level"]) if frame.get("level") else None,
            "audience": str(frame["audience"]) if frame.get("audience") else None,
            "include_dismissed": bool(frame.get("include_dismissed")),
        }
        limit = _int(frame, "limit", DEFAULT_PAGE)
        offset = _int(frame, "offset", 0)
        notifications = await sync_to_async(notification_service.list)(
            user, limit=limit, offset=offset, **arguments
        )
        total = await sync_to_async(notification_service.count)(user, **arguments)
        await self._send_json(
            {
                "type": "list",
                "notifications": notifications,
                "total": total,
                "limit": limit,
                "offset": offset,
            }
        )

    async def _get(self, frame: dict[str, Any]) -> None:
        """One notification by id, dismissed or not."""
        user = self._require_user()
        identifier = _identifier(frame.get("id"))
        try:
            notification = await sync_to_async(notification_service.get)(user, identifier)
        except NotificationNotFound as missing:
            raise SocketError(NOT_FOUND, str(missing)) from None
        await self._send_json({"type": "notification", "notification": notification})

    async def _count(self, frame: dict[str, Any]) -> None:
        """How many rows match a filter, without the rows."""
        user = self._require_user()
        total = await sync_to_async(notification_service.count)(
            user,
            unread=_tristate(frame, "unread"),
            level=str(frame["level"]) if frame.get("level") else None,
            audience=str(frame["audience"]) if frame.get("audience") else None,
            include_dismissed=bool(frame.get("include_dismissed")),
        )
        await self._send_json({"type": "count", "count": total})

    async def _read(self, frame: dict[str, Any]) -> None:
        user = self._require_user()
        result = await sync_to_async(_one)(user, frame, "mark_read")
        await self._send_json({"type": "read", **_result(result)})

    async def _unread_one(self, frame: dict[str, Any]) -> None:
        """Undo a read. Named apart from ``unread`` because that one is the badge."""
        user = self._require_user()
        result = await sync_to_async(_one)(user, frame, "mark_unread")
        await self._send_json({"type": "unread_one", **_result(result)})

    async def _dismiss(self, frame: dict[str, Any]) -> None:
        user = self._require_user()
        result = await sync_to_async(_one)(user, frame, "dismiss")
        await self._send_json({"type": "dismiss", **_result(result)})

    async def _restore(self, frame: dict[str, Any]) -> None:
        user = self._require_user()
        result = await sync_to_async(_one)(user, frame, "restore")
        await self._send_json({"type": "restore", **_result(result)})

    async def _read_all(self, frame: dict[str, Any]) -> None:
        user = self._require_user()
        result = await sync_to_async(notification_service.mark_all_read)(user)
        await self._send_json({"type": "read_all", **result})

    async def _dismiss_all(self, frame: dict[str, Any]) -> None:
        user = self._require_user()
        result = await sync_to_async(notification_service.dismiss_all)(user)
        await self._send_json({"type": "dismiss_all", **result})

    async def _unread(self, frame: dict[str, Any]) -> None:
        user = self._require_user()
        await self._send_json({"type": "unread", "count": await sync_to_async(unread_count)(user)})

    async def _ping(self, frame: dict[str, Any]) -> None:
        """Answered so a client can keep an idle connection alive through a proxy."""
        await self._send_json({"type": "pong"})


def _result(result: dict[str, Any]) -> dict[str, Any]:
    """The reply body every single-notification command shares."""
    return {
        "id": str(result["id"]),
        "unread": result["unread"],
        "changed": result["changed"],
    }


async def notifications_socket(scope: dict[str, Any], receive: Any, send: Any) -> None:
    """The ASGI application this app publishes. Mounted by :mod:`config.sockets`."""
    await NotificationSocket(scope, receive, send).run()
