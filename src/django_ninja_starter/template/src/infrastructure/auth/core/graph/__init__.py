"""GraphQL types shared by every authentication method."""

from infrastructure.auth.core.graph.types import ChallengeType, CredentialsType, LoginType

__all__ = ["ChallengeType", "CredentialsType", "LoginType"]
