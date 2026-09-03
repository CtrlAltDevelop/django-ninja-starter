"""Liveness and readiness over gRPC.

gRPC has no status codes to lean on, so a probe reads ``is_ready`` off the
message rather than off the envelope. The decision is still the service's --
this only frames it.

The actions are asynchronous because the server is (see ``GRPC_FRAMEWORK``), and
the service they call is ordinary synchronous Django, so the call crosses over
with ``sync_to_async``. The generated ``common_pb2`` module is imported inside
the function on purpose: ``manage.py generateproto`` has to import this file
*before* that module exists, and a top-level import would make the first
generation impossible.
"""

from typing import Any

from asgiref.sync import sync_to_async
from django_socio_grpc import generics
from django_socio_grpc.decorators import grpc_action

from infrastructure.common.services import HealthReport, health_service

HEALTH_RESPONSE = [
    {"name": "status", "type": "string"},
    {"name": "is_ready", "type": "bool"},
    {"name": "checks", "type": "map<string,string>"},
]


class HealthService(generics.GenericService):
    """The same two probes the REST router publishes under `/health`."""

    @grpc_action(request=[], response=HEALTH_RESPONSE, response_name="Health")
    async def Liveness(self, request: Any, context: Any) -> Any:
        return _health_message(health_service.liveness())

    @grpc_action(request=[], response=HEALTH_RESPONSE, response_name="Health")
    async def Readiness(self, request: Any, context: Any) -> Any:
        report = await sync_to_async(health_service.readiness)()
        return _health_message(report)


def _health_message(report: HealthReport) -> Any:
    from infrastructure.common.grpc import common_pb2

    return common_pb2.Health(status=report.status, is_ready=report.is_ready, checks=report.checks)


GRPC_SERVICES = [HealthService]
