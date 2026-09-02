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
from apps.notifications.models import Notification, mark_all_read, mark_read, unread_count

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
    endpoint is where the rest lives.
    """
    limit = settings.NOTIFICATIONS_SOCKET_BACKLOG
    if limit <= 0:
        return []
    recent = list(Notification.objects.for_user(user).unread()[:limit])
    return [payload(notification) for notification in reversed(recent)]


def _mark_one_read(user: Any, notification_id: Any) -> int:
    """Mark one visible notification read and return the new unread count."""
    try:
        identifier = UUID(str(notification_id))
    except (TypeError, ValueError):
        raise SocketError(BAD_REQUEST, "That is not a notification id.") from None
    notification = Notification.objects.visible_to(user).filter(pk=identifier).first()
    if notification is None:
        raise SocketError(NOT_FOUND, "No such notification.")
    mark_read(user, notification)
    return unread_count(user)


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
                    await self._send_json(from_broker.result())
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

        handlers = {
            "authenticate": self._authenticate,
            "read": self._read,
            "read_all": self._read_all,
            "unread": self._unread,
            "ping": self._ping,
        }
        command = str(frame.get("command", ""))
        handler = handlers.get(command)
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

    async def _read(self, frame: dict[str, Any]) -> None:
        user = self._require_user()
        identifier = frame.get("id")
        remaining = await sync_to_async(_mark_one_read)(user, identifier)
        await self._send_json({"type": "read", "id": str(identifier), "unread": remaining})

    async def _read_all(self, frame: dict[str, Any]) -> None:
        user = self._require_user()
        changed = await sync_to_async(mark_all_read)(user)
        await self._send_json({"type": "read_all", "count": changed, "unread": 0})

    async def _unread(self, frame: dict[str, Any]) -> None:
        user = self._require_user()
        await self._send_json({"type": "unread", "count": await sync_to_async(unread_count)(user)})

    async def _ping(self, frame: dict[str, Any]) -> None:
        """Answered so a client can keep an idle connection alive through a proxy."""
        await self._send_json({"type": "pong"})


async def notifications_socket(scope: dict[str, Any], receive: Any, send: Any) -> None:
    """The ASGI application this app publishes. Mounted by :mod:`config.sockets`."""
    await NotificationSocket(scope, receive, send).run()
