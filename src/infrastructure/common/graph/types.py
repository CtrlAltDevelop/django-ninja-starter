"""GraphQL types for the health checks.

A GraphQL field cannot answer with a status code, so readiness is a field on the
answer rather than the shape of it. The values are the service's own -- nothing
here re-decides what "ready" means.
"""

import strawberry

from infrastructure.common.services import HealthReport


@strawberry.type
class HealthCheckType:
    """One named dependency and how it answered."""

    name: str
    status: str


@strawberry.type
class HealthType:
    """A probe's whole answer, checks included."""

    status: str
    is_ready: bool
    checks: list[HealthCheckType]

    @classmethod
    def from_report(cls, report: HealthReport) -> "HealthType":
        return cls(
            status=report.status,
            is_ready=report.is_ready,
            checks=[
                HealthCheckType(name=name, status=status)
                for name, status in sorted(report.checks.items())
            ],
        )
