"""Test-wide fixtures: isolation for global auth state, and a real gRPC server."""

import asyncio
from collections.abc import Callable, Iterator
from typing import Any

import grpc
import pytest

from infrastructure.auth.core import delivery
from infrastructure.auth.core.challenges import reset_challenge_store


@pytest.fixture(autouse=True)
def _isolate_auth_state() -> Iterator[None]:
    """Give every test an empty challenge store and an empty outbox.

    The in-memory store and the capturing delivery backends both live for the
    life of the process, so without this a ticket or a code from one test would
    still be sitting there during the next.
    """
    reset_challenge_store()
    delivery.outbox.clear()
    yield
    reset_challenge_store()
    delivery.outbox.clear()


@pytest.fixture
def grpc_call() -> Callable[..., Any]:
    """Call one RPC against a real server carrying this project's own handlers.

    Every gRPC test in this repository goes through here, so what they exercise
    is the generated stub and the registered servicer rather than a Python call
    dressed up as an RPC. The server is asyncio (see ``GRPC_FRAMEWORK``) and the
    tests around it are not, so each call gets its own loop, its own channel and
    a server that is stopped before it returns.

    Pass ``token`` to send the credential the way a gRPC client does: as
    ``authorization`` metadata, which is where
    :func:`infrastructure.common.identity.grpc_caller` looks for it.

    A test that needs data to exist wants ``transactional_db``, not ``db``: the
    server answers on its own connection, and rows sitting in the test's open
    transaction are not there yet as far as that connection is concerned.
    """

    def call(stub_class: Any, method: str, request: Any, token: str | None = None) -> Any:
        from config.grpc import grpc_handlers

        async def run() -> Any:
            server = grpc.aio.server()
            port = server.add_insecure_port("127.0.0.1:0")
            grpc_handlers(server)
            await server.start()
            try:
                async with grpc.aio.insecure_channel(f"127.0.0.1:{port}") as channel:
                    metadata = [("authorization", f"Bearer {token}")] if token else None
                    stub = stub_class(channel)
                    return await getattr(stub, method)(request, metadata=metadata)
            finally:
                await server.stop(None)

        return asyncio.run(run())

    return call
