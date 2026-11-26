"""The GraphQL transport for the webhooks app."""

from apps.webhooks.graph.schema import Mutation, Query

__all__ = ["Mutation", "Query"]
