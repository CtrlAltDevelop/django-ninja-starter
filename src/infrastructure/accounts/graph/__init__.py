"""The GraphQL transport for the accounts app."""

from infrastructure.accounts.graph.schema import Mutation, Query

__all__ = ["Mutation", "Query"]
