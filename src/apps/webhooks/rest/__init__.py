"""The HTTP transport for the webhooks app."""

from apps.webhooks.rest.v1 import router

__all__ = ["router"]
