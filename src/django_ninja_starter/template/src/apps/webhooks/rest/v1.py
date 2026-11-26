"""Managing webhook endpoints over HTTP. Every decision is in ``services``."""

from typing import Any

from django.http import HttpRequest
from ninja import Router

from apps.webhooks import services
from apps.webhooks.rest.schemas import DeliveryOut, EndpointIn, EndpointOut

try:  # pragma: no cover - exercised by whichever branch the project installs
    from infrastructure.auth.core.sessions import api_auth
except ImportError:  # pragma: no cover - only in a project without the auth apps
    from ninja.security import django_auth as api_auth  # type: ignore[assignment]

router = Router(auth=api_auth)


@router.get("", response=list[EndpointOut], summary="List this account's webhook endpoints")
def list_endpoints(request: HttpRequest) -> list[dict[str, Any]]:
    return services.list_endpoints(request.user)


@router.post("", response={201: EndpointOut}, summary="Register a webhook endpoint")
def create_endpoint(request: HttpRequest, payload: EndpointIn) -> tuple[int, dict[str, Any]]:
    """The answer carries the signing secret; it is not shown again except on rotation."""
    return 201, services.create_endpoint(request.user, payload.url, payload.events)


@router.delete("/{endpoint_id}", response={204: None}, summary="Delete a webhook endpoint")
def delete_endpoint(request: HttpRequest, endpoint_id: str) -> tuple[int, None]:
    services.delete_endpoint(request.user, endpoint_id)
    return 204, None


@router.post(
    "/{endpoint_id}/rotate-secret", response=EndpointOut, summary="Replace the signing secret"
)
def rotate_secret(request: HttpRequest, endpoint_id: str) -> dict[str, Any]:
    return services.rotate_secret(request.user, endpoint_id)


@router.get(
    "/{endpoint_id}/deliveries",
    response=list[DeliveryOut],
    summary="Recent deliveries to an endpoint, with every attempt",
)
def list_deliveries(
    request: HttpRequest, endpoint_id: str, limit: int = 50
) -> list[dict[str, Any]]:
    return services.list_deliveries(request.user, endpoint_id, limit)
