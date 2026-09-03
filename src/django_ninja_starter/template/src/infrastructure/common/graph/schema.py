"""The health app's contribution to the project's GraphQL schema."""

import strawberry

from infrastructure.common.graph.errors import resolver
from infrastructure.common.graph.types import HealthType
from infrastructure.common.services import health_service


@strawberry.type
class Query:
    """Read-only health fields, merged into the root query by `config.graph`."""

    @strawberry.field(description="Whether this process can serve requests at all.")
    @resolver
    def liveness(self) -> HealthType:
        return HealthType.from_report(health_service.liveness())

    @strawberry.field(description="Whether the infrastructure this process needs is reachable.")
    @resolver
    def readiness(self) -> HealthType:
        return HealthType.from_report(health_service.readiness())
