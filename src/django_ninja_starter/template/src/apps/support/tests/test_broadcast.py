"""Fan-out: the one part of this app that is not ordinary Django.

The memory broker is exercised directly, including from another thread, because
that is how it is really used -- a view or an agent's admin save publishes on a
worker thread while the socket waits on an event loop somewhere else. The Redis
broker is exercised against fakeredis, so the pub/sub handling is covered
without a server.
"""

import asyncio
import threading
from typing import Any

import fakeredis
import pytest
from django.core.exceptions import ImproperlyConfigured
from django.test import override_settings

from apps.support.broadcast import (
    MemoryBroker,
    RedisBroker,
    get_broker,
    reset_broker,
    staff_channel,
    ticket_channel,
    user_channel,
)


def test_the_channel_names_follow_the_configured_prefix() -> None:
    """The three audiences are three channel shapes, and all of them move together."""
    with override_settings(SUPPORT_CHANNEL_PREFIX="desk"):
        assert ticket_channel("7") == "desk:ticket:7"
        assert user_channel("abc") == "desk:user:abc"
        assert staff_channel() == "desk:staff"


def test_a_payload_reaches_a_subscriber_of_that_channel() -> None:
    async def scenario() -> dict[str, Any]:
        broker = MemoryBroker()
        subscription = broker.subscribe()
        await subscription.add("one")
        broker.publish("one", {"hello": "world"})
        return await asyncio.wait_for(subscription.get(), timeout=2)

    assert asyncio.run(scenario()) == {"hello": "world"}


def test_a_payload_does_not_reach_a_subscriber_of_another_channel() -> None:
    """A ticket channel carrying into another conversation would be the worst bug here."""

    async def scenario() -> bool:
        broker = MemoryBroker()
        subscription = broker.subscribe()
        await subscription.add(ticket_channel(1))
        broker.publish(ticket_channel(2), {"hello": "world"})
        try:
            await asyncio.wait_for(subscription.get(), timeout=0.2)
        except TimeoutError:
            return True
        return False

    assert asyncio.run(scenario()) is True


def test_a_channel_added_after_subscribing_starts_delivering() -> None:
    """The socket's ``subscribe`` command: joining a ticket on a live connection."""

    async def scenario() -> dict[str, Any]:
        broker = MemoryBroker()
        subscription = broker.subscribe()
        await subscription.add(user_channel("abc"))
        await subscription.add(ticket_channel(1))
        broker.publish(ticket_channel(1), {"joined": True})
        return await asyncio.wait_for(subscription.get(), timeout=2)

    assert asyncio.run(scenario()) == {"joined": True}


def test_one_payload_reaches_every_subscriber_of_the_channel() -> None:
    """The desk channel's whole purpose: a new ticket reaching agents not in it yet."""

    async def scenario() -> list[dict[str, Any]]:
        broker = MemoryBroker()
        agents = [broker.subscribe() for _ in range(3)]
        for agent in agents:
            await agent.add(staff_channel())
        broker.publish(staff_channel(), {"opened": "SUP-1"})
        return [await asyncio.wait_for(agent.get(), timeout=2) for agent in agents]

    assert asyncio.run(scenario()) == [{"opened": "SUP-1"}] * 3


def test_publishing_from_another_thread_reaches_the_loop() -> None:
    """Which is the real arrangement: a request thread publishing to a socket's loop."""

    async def scenario() -> dict[str, Any]:
        broker = MemoryBroker()
        subscription = broker.subscribe()
        await subscription.add("one")
        threading.Thread(target=broker.publish, args=("one", {"threaded": True})).start()
        return await asyncio.wait_for(subscription.get(), timeout=2)

    assert asyncio.run(scenario()) == {"threaded": True}


def test_a_closed_subscription_stops_being_published_to() -> None:
    async def scenario() -> int:
        broker = MemoryBroker()
        subscription = broker.subscribe()
        await subscription.add("one")
        await subscription.close()
        broker.publish("one", {"hello": "world"})
        return len(broker._subscriptions)

    assert asyncio.run(scenario()) == 0


def test_a_subscription_whose_loop_has_gone_drops_itself() -> None:
    """A connection can vanish without anybody closing it; publishing must not raise."""
    broker = MemoryBroker()
    subscription = asyncio.run(_subscribe_and_abandon(broker))

    broker.publish("one", {"hello": "world"})

    assert subscription not in broker._subscriptions


async def _subscribe_and_abandon(broker: MemoryBroker) -> Any:
    subscription = broker.subscribe()
    await subscription.add("one")
    return subscription


def test_the_redis_broker_publishes_json_to_the_channel() -> None:
    client = fakeredis.FakeStrictRedis()
    broker = RedisBroker(client=client)
    pubsub = client.pubsub()
    pubsub.subscribe("one")

    broker.publish("one", {"hello": "world"})

    messages = [pubsub.get_message() for _ in range(3)]
    assert any(
        message and message["type"] == "message" and b"world" in message["data"]
        for message in messages
    )


def test_a_redis_subscriber_receives_what_was_published() -> None:
    server = fakeredis.FakeServer()

    async def scenario() -> dict[str, Any]:
        broker = RedisBroker(
            client=fakeredis.FakeStrictRedis(server=server),
            async_client=fakeredis.aioredis.FakeRedis(server=server),
        )
        subscription = broker.subscribe()
        await subscription.add("one")
        reader = asyncio.ensure_future(subscription.get())
        # The reader joins the channel on its first pass, so publishing has to
        # wait for it -- the same lag a real deployment has, made explicit.
        await asyncio.sleep(0.3)
        broker.publish("one", {"hello": "world"})
        try:
            return await asyncio.wait_for(reader, timeout=5)
        finally:
            await subscription.close()

    assert asyncio.run(scenario()) == {"hello": "world"}


def test_a_redis_channel_joined_mid_read_starts_delivering() -> None:
    """``add`` while the reader is already polling is the socket's normal case."""
    server = fakeredis.FakeServer()

    async def scenario() -> dict[str, Any]:
        broker = RedisBroker(
            client=fakeredis.FakeStrictRedis(server=server),
            async_client=fakeredis.aioredis.FakeRedis(server=server),
        )
        subscription = broker.subscribe()
        await subscription.add(user_channel("abc"))
        reader = asyncio.ensure_future(subscription.get())
        await asyncio.sleep(0.3)
        await subscription.add(ticket_channel(1))
        await asyncio.sleep(0.3)
        broker.publish(ticket_channel(1), {"joined": True})
        try:
            return await asyncio.wait_for(reader, timeout=5)
        finally:
            await subscription.close()

    assert asyncio.run(scenario()) == {"joined": True}


def test_closing_a_redis_subscription_twice_is_harmless() -> None:
    """A socket that errors and then disconnects closes on both paths."""
    server = fakeredis.FakeServer()

    async def scenario() -> None:
        broker = RedisBroker(
            client=fakeredis.FakeStrictRedis(server=server),
            async_client=fakeredis.aioredis.FakeRedis(server=server),
        )
        subscription = broker.subscribe()
        await subscription.add("one")
        await subscription.close()
        await subscription.close()

    asyncio.run(scenario())


def test_the_broker_is_built_once_and_rebuilt_when_the_setting_changes() -> None:
    reset_broker()
    first = get_broker()

    assert get_broker() is first

    with override_settings(SUPPORT_BROKER="apps.support.broadcast.MemoryBroker"):
        assert get_broker() is not first


def test_a_broker_that_cannot_be_imported_is_a_configuration_error() -> None:
    with (
        override_settings(SUPPORT_BROKER="apps.support.broadcast.NoSuchBroker"),
        pytest.raises(ImproperlyConfigured, match="not importable"),
    ):
        get_broker()


def test_the_redis_clients_are_opened_once_from_the_configured_url() -> None:
    """Deferring both imports is what lets redis stay an optional dependency."""
    from unittest.mock import patch

    broker = RedisBroker()
    with override_settings(SUPPORT_REDIS_URL="redis://example.test:6379/3"):
        with patch("redis.Redis.from_url", return_value=fakeredis.FakeStrictRedis()) as sync:
            assert broker.client() is broker.client()
        with patch(
            "redis.asyncio.Redis.from_url", return_value=fakeredis.aioredis.FakeRedis()
        ) as asynchronous:
            assert broker.async_client() is broker.async_client()

    sync.assert_called_once_with("redis://example.test:6379/3")
    asynchronous.assert_called_once_with("redis://example.test:6379/3")
