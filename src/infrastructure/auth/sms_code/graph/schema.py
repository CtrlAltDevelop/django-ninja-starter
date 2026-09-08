"""SMS-code authentication, over GraphQL.

All mutations: each one sends a code, spends one, or retires a credential. The
two `start` fields answer identically whether or not a phone number has an
account, exactly as the routes do -- that is :class:`sms_code_service`'s decision, not
this file's.
"""

from typing import Any

import strawberry
from strawberry.types import Info

from infrastructure.auth.core.graph.types import ChallengeType, LoginType, MessageType
from infrastructure.auth.sms_code.services import Challenge, sms_code_service
from infrastructure.common.graph.errors import resolver


def challenge_type(challenge: Challenge) -> ChallengeType:
    return ChallengeType(
        ticket=challenge.ticket,
        channel=challenge.channel,
        destination=challenge.destination,
        expires_in=challenge.expires_in,
    )


@strawberry.type
class Mutation:
    @strawberry.mutation(description="Send a sign-up code.")
    @resolver
    def sms_code_signup_start(self, info: Info[Any, Any], phone: str) -> ChallengeType:
        return challenge_type(sms_code_service.start_signup(info.context.request, phone=phone))

    @strawberry.mutation(description="Create an account with a code.")
    @resolver
    def sms_code_signup_verify(self, info: Info[Any, Any], ticket: str, code: str) -> LoginType:
        return LoginType.from_result(
            sms_code_service.verify_signup(info.context.request, ticket=ticket, code=code)
        )

    @strawberry.mutation(description="Send a sign-in code.")
    @resolver
    def sms_code_login_start(self, info: Info[Any, Any], phone: str) -> ChallengeType:
        return challenge_type(sms_code_service.start_login(info.context.request, phone=phone))

    @strawberry.mutation(description="Sign in with a code.")
    @resolver
    def sms_code_login_verify(self, info: Info[Any, Any], ticket: str, code: str) -> LoginType:
        return LoginType.from_result(
            sms_code_service.verify_login(info.context.request, ticket=ticket, code=code)
        )

    @strawberry.mutation(description="Sign out, retiring the presented credential.")
    @resolver
    def sms_code_logout(self, info: Info[Any, Any], token: str = "") -> MessageType:
        return MessageType(detail=sms_code_service.logout(info.context.request, token=token))
