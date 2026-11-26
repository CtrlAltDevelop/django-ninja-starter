"""The webhooks app's GraphQL fields, mirroring the REST endpoints one for one."""

from typing import Any

import strawberry
from strawberry.types import Info

from apps.webhooks import services
from infrastructure.common.graph.errors import require_caller, resolver
from infrastructure.common.identity import caller


@strawberry.type
class WebhookEndpointType:
    id: str
    url: str
    events: list[str]
    is_active: bool
    created_at: str
    secret: str


@strawberry.type
class WebhookAttemptType:
    number: int
    status_code: int
    error: str
    duration_ms: int
    created_at: str


@strawberry.type
class WebhookDeliveryType:
    id: str
    event: str
    status: str
    attempts: int
    next_attempt_at: str
    created_at: str
    attempt_log: list[WebhookAttemptType]


def _caller(info: Info[Any, Any]) -> Any:
    return require_caller(caller(info.context.request))


def _endpoint(row: dict[str, Any]) -> WebhookEndpointType:
    return WebhookEndpointType(**row)


def _delivery(row: dict[str, Any]) -> WebhookDeliveryType:
    attempts = [WebhookAttemptType(**attempt) for attempt in row["attempt_log"]]
    return WebhookDeliveryType(**{**row, "attempt_log": attempts})


@strawberry.type
class Query:
    @strawberry.field(description="This account's webhook endpoints.")
    @resolver
    def webhook_endpoints(self, info: Info[Any, Any]) -> list[WebhookEndpointType]:
        return [_endpoint(row) for row in services.list_endpoints(_caller(info))]

    @strawberry.field(description="Recent deliveries to one endpoint, with every attempt.")
    @resolver
    def webhook_deliveries(
        self, info: Info[Any, Any], endpoint_id: str, limit: int = 50
    ) -> list[WebhookDeliveryType]:
        rows = services.list_deliveries(_caller(info), endpoint_id, limit)
        return [_delivery(row) for row in rows]


@strawberry.type
class Mutation:
    @strawberry.mutation(description="Register an endpoint. The answer carries its secret.")
    @resolver
    def create_webhook_endpoint(
        self, info: Info[Any, Any], url: str, events: list[str]
    ) -> WebhookEndpointType:
        return _endpoint(services.create_endpoint(_caller(info), url, events))

    @strawberry.mutation(description="Delete an endpoint and its delivery record.")
    @resolver
    def delete_webhook_endpoint(self, info: Info[Any, Any], endpoint_id: str) -> bool:
        services.delete_endpoint(_caller(info), endpoint_id)
        return True

    @strawberry.mutation(description="Replace an endpoint's signing secret.")
    @resolver
    def rotate_webhook_secret(self, info: Info[Any, Any], endpoint_id: str) -> WebhookEndpointType:
        return _endpoint(services.rotate_secret(_caller(info), endpoint_id))
