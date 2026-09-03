"""Health checks, expressed once for every transport that asks for them.

The service is the only place that knows *what* readiness means. REST, GraphQL
and gRPC each know how to say it -- a status code, a field, a status message --
and none of them repeats the check itself. That is the rule the whole project
follows: a transport package renders, a service decides.
"""

from dataclasses import dataclass
from typing import Literal

from django.db import connections
from django.db.utils import OperationalError

# The whole vocabulary a probe answers in. Narrow on purpose: every transport
# renders these two words, and a third would have to be added here first.
type HealthStatus = Literal["ok", "unavailable"]


@dataclass(frozen=True, slots=True)
class HealthReport:
    """What a probe learned, before any transport has framed it."""

    status: HealthStatus
    checks: dict[str, HealthStatus]

    @property
    def is_ready(self) -> bool:
        return self.status == "ok"


class HealthService:
    """Liveness and readiness, for a load balancer and an orchestrator."""

    OK: HealthStatus = "ok"
    UNAVAILABLE: HealthStatus = "unavailable"

    def database_is_ready(self, alias: str = "default") -> bool:
        """Return whether the configured database accepts a trivial query."""
        try:
            with connections[alias].cursor() as cursor:
                cursor.execute("SELECT 1")
                cursor.fetchone()
        except OperationalError:
            return False
        return True

    def liveness(self) -> HealthReport:
        """Report whether this process can serve requests at all.

        Deliberately checks nothing: a liveness probe that fails when the
        database is down gets the healthy web process restarted, which is the
        opposite of the repair anybody wanted.
        """
        return HealthReport(status=self.OK, checks={})

    def readiness(self) -> HealthReport:
        """Report whether the infrastructure this process needs is reachable."""
        state: HealthStatus = self.OK if self.database_is_ready() else self.UNAVAILABLE
        return HealthReport(status=state, checks={"database": state})


health_service = HealthService()


def database_is_ready(alias: str = "default") -> bool:
    """Back-compatible alias for :meth:`HealthService.database_is_ready`."""
    return health_service.database_is_ready(alias)
