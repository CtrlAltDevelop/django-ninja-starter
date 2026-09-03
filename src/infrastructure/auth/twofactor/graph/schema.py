"""Second factors, over GraphQL.

The one query is what an account has enrolled; everything else changes
something. The login-time pair (`twofactorChallenge`, `twofactorVerify`) needs
no credential -- a caller in the middle of a sign-in does not have one yet, and
the login ticket is what stands in for it.
"""

from typing import Any

import strawberry
from strawberry.types import Info

from infrastructure.auth.core.graph.types import (
    ChallengeType,
    CredentialsType,
    LoginType,
    MessageType,
)
from infrastructure.auth.twofactor.services import SentCode, twofactor_service
from infrastructure.common.graph.errors import require_caller, resolver
from infrastructure.common.identity import caller


@strawberry.type
class EnrolledFactorType:
    method: str
    destination: str
    confirmed: bool
    last_used_at: str


@strawberry.type
class FactorListType:
    """What is enrolled, what this deployment allows, and what is left in reserve."""

    methods: list[EnrolledFactorType]
    available: list[str]
    unused_recovery_codes: int


@strawberry.type
class TotpEnrolmentType:
    """The shared secret, in the two forms an authenticator app accepts."""

    secret: str
    otpauth_uri: str


@strawberry.type
class RecoveryCodesType:
    """Shown once. The server keeps only digests from here on."""

    codes: list[str]


def challenge_type(sent: SentCode) -> ChallengeType:
    return ChallengeType(
        ticket=sent.ticket,
        channel=sent.channel,
        destination=sent.destination,
        expires_in=sent.expires_in,
    )


def _caller(info: Info[Any, Any]) -> Any:
    return require_caller(caller(info.context.request))


@strawberry.type
class Query:
    @strawberry.field(description="The second factors on the signed-in account.")
    @resolver
    def second_factors(self, info: Info[Any, Any]) -> FactorListType:
        factors = twofactor_service.factors(_caller(info))
        return FactorListType(
            methods=[
                EnrolledFactorType(
                    method=factor.method,
                    destination=factor.destination,
                    confirmed=factor.confirmed,
                    last_used_at=factor.last_used_at,
                )
                for factor in factors.methods
            ],
            available=factors.available,
            unused_recovery_codes=factors.unused_recovery_codes,
        )


@strawberry.type
class Mutation:
    @strawberry.mutation(description="Send a code for a sign-in awaiting a second factor.")
    @resolver
    def twofactor_challenge(
        self, info: Info[Any, Any], login_ticket: str, method: str
    ) -> ChallengeType:
        return challenge_type(
            twofactor_service.challenge(
                info.context.request, login_ticket=login_ticket, method=method
            )
        )

    @strawberry.mutation(description="Finish a sign-in with a second factor.")
    @resolver
    def twofactor_verify(
        self, info: Info[Any, Any], login_ticket: str, code: str, method: str = ""
    ) -> LoginType:
        credentials = twofactor_service.verify(
            info.context.request, login_ticket=login_ticket, code=code, method=method
        )
        return LoginType(
            requires_second_factor=False,
            credentials=CredentialsType.from_issued(credentials),
            login_ticket="",
            methods=[],
        )

    @strawberry.mutation(description="Start authenticator-app enrolment.")
    @resolver
    def twofactor_totp_enroll(self, info: Info[Any, Any]) -> TotpEnrolmentType:
        enrolment = twofactor_service.start_totp(_caller(info))
        return TotpEnrolmentType(secret=enrolment.secret, otpauth_uri=enrolment.otpauth_uri)

    @strawberry.mutation(description="Confirm authenticator-app enrolment.")
    @resolver
    def twofactor_totp_confirm(self, info: Info[Any, Any], code: str) -> MessageType:
        request = info.context.request
        return MessageType(detail=twofactor_service.confirm_totp(request, _caller(info), code=code))

    @strawberry.mutation(description="Start SMS second-factor enrolment.")
    @resolver
    def twofactor_sms_enroll(self, info: Info[Any, Any], phone: str) -> ChallengeType:
        return challenge_type(twofactor_service.start_sms(_caller(info), phone=phone))

    @strawberry.mutation(description="Confirm SMS second-factor enrolment.")
    @resolver
    def twofactor_sms_confirm(self, info: Info[Any, Any], ticket: str, code: str) -> MessageType:
        request = info.context.request
        return MessageType(
            detail=twofactor_service.confirm_sms(request, _caller(info), ticket=ticket, code=code)
        )

    @strawberry.mutation(description="Start email second-factor enrolment.")
    @resolver
    def twofactor_email_enroll(self, info: Info[Any, Any]) -> ChallengeType:
        return challenge_type(twofactor_service.start_email(_caller(info)))

    @strawberry.mutation(description="Confirm email second-factor enrolment.")
    @resolver
    def twofactor_email_confirm(self, info: Info[Any, Any], ticket: str, code: str) -> MessageType:
        request = info.context.request
        return MessageType(
            detail=twofactor_service.confirm_email(request, _caller(info), ticket=ticket, code=code)
        )

    @strawberry.mutation(description="Replace the recovery codes on this account.")
    @resolver
    def twofactor_recovery_generate(self, info: Info[Any, Any]) -> RecoveryCodesType:
        request = info.context.request
        return RecoveryCodesType(
            codes=twofactor_service.generate_recovery_codes(request, _caller(info))
        )

    @strawberry.mutation(description="Turn off a second factor.")
    @resolver
    def twofactor_remove(self, info: Info[Any, Any], method: str) -> MessageType:
        request = info.context.request
        return MessageType(detail=twofactor_service.remove(request, _caller(info), method=method))
