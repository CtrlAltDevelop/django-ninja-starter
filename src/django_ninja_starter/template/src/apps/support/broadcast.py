"""Getting what happened in a thread out to the sockets that are in it.

A message is posted in a request -- ordinary synchronous Django code, or an
agent saving a reply in the admin -- and has to reach WebSocket connections
being held open somewhere else entirely, possibly in another worker process.
That is a fan-out problem, and it is the only genuinely hard part of this app,
so it lives behind one small interface with two implementations.

:class:`MemoryBroker` keeps subscribers in a set and hands each one the payload
directly. It needs nothing installed, which is what lets the app work the moment
it is enabled, and it fans out *within one process only*. Under two workers, a
client connected to the first never hears the agent replying on the second --
which for a chat app is not a degraded experience but a broken one. That is why
the app's settings contract warns about it rather than leaving it to be
discovered by two people staring at each other's silence.

:class:`RedisBroker` publishes to a Redis channel and every process subscribed
to it wakes up, which is the same thing done across a deployment.

The interface is deliberately asymmetric. **Publishing is synchronous**, because
the code that posts a message is: a view, a signal handler, a management
command. **Subscribing is asynchronous**, because the code that consumes one is
a socket. Nothing here bridges the two by starting an event loop or a thread
pool; the memory broker hands work to the subscriber's own loop with
``call_soon_threadsafe``, and the Redis broker lets Redis be the bridge.

**Three kinds of channel**, because this app has three audiences and they are
not the same shape:

* a **thread** channel, which everybody in one conversation is on. This is the
  one chat actually flows over.
* an **account** channel, which one person's every connection is on. It carries
  what concerns them across all their threads -- a badge, a thread they have
  just been added to -- so a client does not have to subscribe to a channel per
  thread before it knows the threads exist.
* the **desk** channel, which every member of staff is on. A new ticket has to
  reach agents who are not in it yet, and by definition cannot arrive on its
  thread channel or on any one agent's.

A subscription's channel set changes while it is being read -- that is the whole
point of the socket's ``subscribe`` command, which joins a thread channel on a
connection that already has the others. Both implementations therefore accept
:meth:`add` at any time, including from another task.
"""

import asyncio
import json
import threading
from typing import TYPE_CHECKING, Any, Protocol

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.core.signals import setting_changed
from django.dispatch import receiver
from django.utils.module_loading import import_string

if TYPE_CHECKING:  # pragma: no cover
    import redis.asyncio

type Payload = dict[str, Any]

# How long the Redis reader waits for a message before looking at whether the
# connection has asked to join another channel. It is a poll rather than a
# second task because one task owning the connection is what makes concurrent
# `add` safe; the cost is that joining a thread can lag by this much.
POLL_SECONDS = 0.25


def channel_prefix() -> str:
    return settings.SUPPORT_CHANNEL_PREFIX


def ticket_channel(ticket_id: Any) -> str:
    """The channel everybody in one conversation is on."""
    return f"{channel_prefix()}:ticket:{ticket_id}"


def user_channel(user_id: Any) -> str:
    """The channel one account's every connection is on, once it has proven who it is."""
    return f"{channel_prefix()}:user:{user_id}"


def staff_channel() -> str:
    """The channel every member of staff is on. Where a new ticket is announced."""
    return f"{channel_prefix()}:staff"


class Subscription(Protocol):
    async def add(self, channel: str) -> None: ...

    async def get(self) -> Payload: ...

    async def close(self) -> None: ...


class Broker(Protocol):
    def subscribe(self) -> Subscription: ...

    def publish(self, channel: str, payload: Payload) -> None: ...


class MemorySubscription:
    """A queue fed straight from :meth:`MemoryBroker.publish`, in whatever thread it ran."""

    def __init__(self, broker: "MemoryBroker") -> None:
        self._broker = broker
        self._loop = asyncio.get_running_loop()
        self._queue: asyncio.Queue[Payload] = asyncio.Queue()
        self.channels: set[str] = set()

    async def add(self, channel: str) -> None:
        self.channels.add(channel)

    async def get(self) -> Payload:
        return await self._queue.get()

    async def close(self) -> None:
        self._broker.forget(self)

    def deliver(self, payload: Payload) -> None:
        """Hand a payload to the subscriber's event loop from the publishing thread.

        A loop that has already closed means the connection is gone and nobody
        removed it, so the subscription drops itself rather than raising into
        whichever request happened to be publishing.
        """
        try:
            self._loop.call_soon_threadsafe(self._queue.put_nowait, payload)
        except RuntimeError:
            self._broker.forget(self)


class MemoryBroker:
    """Fan-out inside one process. The default, and never the right production answer."""

    def __init__(self) -> None:
        self._subscriptions: set[MemorySubscription] = set()
        self._lock = threading.Lock()

    def subscribe(self) -> MemorySubscription:
        subscription = MemorySubscription(self)
        with self._lock:
            self._subscriptions.add(subscription)
        return subscription

    def forget(self, subscription: MemorySubscription) -> None:
        with self._lock:
            self._subscriptions.discard(subscription)

    def publish(self, channel: str, payload: Payload) -> None:
        with self._lock:
            listening = [
                subscription
                for subscription in self._subscriptions
                if channel in subscription.channels
            ]
        for subscription in listening:
            subscription.deliver(payload)


class RedisSubscription:
    """One Redis pub/sub connection, read by exactly one task.

    ``add`` records a channel and returns; the reader joins it on its next pass.
    Doing the subscribe here rather than in ``add`` is what keeps every command
    on this connection in a single task, which is the only way concurrent use of
    a pub/sub connection is safe.
    """

    def __init__(self, broker: "RedisBroker") -> None:
        self._broker = broker
        self._pubsub: Any | None = None
        self._pending: list[str] = []
        self._joined: set[str] = set()

    async def add(self, channel: str) -> None:
        if channel not in self._joined and channel not in self._pending:
            self._pending.append(channel)

    async def _join_pending(self) -> None:
        if not self._pending:
            return
        if self._pubsub is None:
            self._pubsub = self._broker.async_client().pubsub()
        joining, self._pending = self._pending, []
        await self._pubsub.subscribe(*joining)
        self._joined.update(joining)

    async def get(self) -> Payload:
        while True:
            await self._join_pending()
            if self._pubsub is None:
                await asyncio.sleep(POLL_SECONDS)
                continue
            message = await self._pubsub.get_message(
                ignore_subscribe_messages=True, timeout=POLL_SECONDS
            )
            if message is not None and message.get("type") == "message":
                return json.loads(message["data"])

    async def close(self) -> None:
        if self._pubsub is not None:
            await self._pubsub.aclose()
            self._pubsub = None


class RedisBroker:
    """Fan-out across every process pointed at the same Redis.

    Two clients, because the two halves of this app live on different sides of
    the sync/async divide: publishing happens in a view, subscribing in a
    socket. Both are built on first use, so choosing the other broker never
    imports a driver it will not open.
    """

    def __init__(self, client: Any | None = None, async_client: Any | None = None) -> None:
        self._client = client
        self._async_client = async_client

    def client(self) -> Any:
        if self._client is None:
            import redis

            self._client = redis.Redis.from_url(settings.SUPPORT_REDIS_URL)
        return self._client

    def async_client(self) -> "redis.asyncio.Redis":
        if self._async_client is None:
            import redis.asyncio

            self._async_client = redis.asyncio.Redis.from_url(settings.SUPPORT_REDIS_URL)
        return self._async_client

    def subscribe(self) -> RedisSubscription:
        return RedisSubscription(self)

    def publish(self, channel: str, payload: Payload) -> None:
        self.client().publish(channel, json.dumps(payload))


_broker: Broker | None = None


def get_broker() -> Broker:
    """Return the configured broker, built once per process."""
    global _broker
    if _broker is None:
        try:
            _broker = import_string(settings.SUPPORT_BROKER)()
        except ImportError as error:
            raise ImproperlyConfigured(
                f"DJANGO_SUPPORT_BROKER is not importable: {settings.SUPPORT_BROKER}"
            ) from error
    return _broker


def reset_broker() -> None:
    global _broker
    _broker = None


@receiver(setting_changed)
def _reset_on_setting_change(setting: str, **kwargs: object) -> None:
    if setting in {"SUPPORT_BROKER", "SUPPORT_REDIS_URL"}:
        reset_broker()
