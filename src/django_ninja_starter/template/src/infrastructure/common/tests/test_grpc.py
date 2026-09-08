"""The health checks, asked for over gRPC rather than over HTTP.

Driven through the ``grpc_call`` fixture, which stands up a real asyncio server
carrying this project's own handlers -- so what is exercised is the generated
stub and the registered servicer.

Every test here takes a database, including the one whose RPC touches nothing:
django-socio-grpc runs each call through Django's middleware, which reads the
session, so an RPC reaches the database before it reaches a service.
"""

from collections.abc import Callable
from typing import Any
from unittest.mock import patch

from google.protobuf.empty_pb2 import Empty

from infrastructure.common.grpc import common_pb2, common_pb2_grpc
from infrastructure.common.services import HealthService

Stub = common_pb2_grpc.HealthControllerStub


def test_liveness_answers_over_a_real_channel(
    transactional_db: None, grpc_call: Callable[..., Any]
) -> None:
    reply = grpc_call(Stub, "Liveness", Empty())

    assert reply.status == "ok"
    assert reply.is_ready is True
    assert dict(reply.checks) == {}


def test_readiness_reports_the_database_it_checked(
    transactional_db: None, grpc_call: Callable[..., Any]
) -> None:
    reply = grpc_call(Stub, "Readiness", Empty())

    assert reply.status == "ok"
    assert dict(reply.checks) == {"database": "ok"}


def test_an_unready_database_is_a_field_here_too(
    transactional_db: None, grpc_call: Callable[..., Any]
) -> None:
    """Three transports, one decision: gRPC says it in the message, as GraphQL does."""
    with patch.object(HealthService, "database_is_ready", return_value=False):
        reply = grpc_call(Stub, "Readiness", Empty())

    assert reply.status == "unavailable"
    assert reply.is_ready is False


def test_the_generated_proto_is_in_step_with_the_service() -> None:
    """A stale `.proto` is a silent contract break, so the message is pinned here."""
    fields = {field.name for field in common_pb2.Health.DESCRIPTOR.fields}

    assert fields == {"status", "is_ready", "checks"}
