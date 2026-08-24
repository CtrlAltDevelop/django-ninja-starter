from django.http import HttpRequest
from ninja import Router, Status

from infrastructure.common.schemas import HealthResponse
from infrastructure.common.services import database_is_ready

router = Router()


@router.get(
    "/live",
    response=HealthResponse,
    auth=None,
    summary="Liveness check",
)
def liveness(request: HttpRequest) -> HealthResponse:
    """Report whether the web process can serve requests."""
    return HealthResponse(status="ok", checks={})


@router.get(
    "/ready",
    response={200: HealthResponse, 503: HealthResponse},
    auth=None,
    summary="Readiness check",
)
def readiness(request: HttpRequest) -> Status[HealthResponse]:
    """Report whether required infrastructure is available."""
    database_ready = database_is_ready()
    response = HealthResponse(
        status="ok" if database_ready else "unavailable",
        checks={"database": "ok" if database_ready else "unavailable"},
    )
    return Status(200 if database_ready else 503, response)
