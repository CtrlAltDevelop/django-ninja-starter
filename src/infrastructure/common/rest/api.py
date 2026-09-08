"""Liveness and readiness over HTTP.

Everything decided here is decided by :class:`HealthService`. What is left is
the part that is genuinely REST: which status code carries a report that says
the process is not ready.
"""

from django.http import HttpRequest
from ninja import Router, Status

from infrastructure.common.rest.schemas import HealthResponse
from infrastructure.common.services import health_service

router = Router()


@router.get(
    "/live",
    response=HealthResponse,
    auth=None,
    summary="Liveness check",
)
def liveness(request: HttpRequest) -> HealthResponse:
    """Report whether the web process can serve requests."""
    report = health_service.liveness()
    return HealthResponse(status=report.status, checks=report.checks)


@router.get(
    "/ready",
    response={200: HealthResponse, 503: HealthResponse},
    auth=None,
    summary="Readiness check",
)
def readiness(request: HttpRequest) -> Status[HealthResponse]:
    """Report whether required infrastructure is available."""
    report = health_service.readiness()
    response = HealthResponse(status=report.status, checks=report.checks)
    return Status(200 if report.is_ready else 503, response)
