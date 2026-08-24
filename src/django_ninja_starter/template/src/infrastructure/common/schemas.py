"""Schemas shared by infrastructure endpoints."""

from typing import Literal

from ninja import Schema


class HealthResponse(Schema):
    """Stable health-check response contract."""

    status: Literal["ok", "unavailable"]
    checks: dict[str, Literal["ok", "unavailable"]]
