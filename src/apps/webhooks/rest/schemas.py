"""The contract the webhook endpoints publish."""

from ninja import Schema


class EndpointIn(Schema):
    url: str
    events: list[str]


class EndpointOut(Schema):
    """``secret`` is filled only when created or rotated, and empty otherwise."""

    id: str
    url: str
    events: list[str]
    is_active: bool
    created_at: str
    secret: str


class AttemptOut(Schema):
    number: int
    status_code: int
    error: str
    duration_ms: int
    created_at: str


class DeliveryOut(Schema):
    id: str
    event: str
    status: str
    attempts: int
    next_attempt_at: str
    created_at: str
    attempt_log: list[AttemptOut]
