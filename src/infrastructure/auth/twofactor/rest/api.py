"""Enrolling and retiring the second factors on a signed-in account.

The login-time half of this app is next door, in ``login.py``.
"""

from django.http import HttpRequest
from ninja import Router

from infrastructure.auth.core.rest.schemas import ChallengeOut, MessageOut
from infrastructure.auth.core.sessions import api_auth
from infrastructure.auth.twofactor.rest.login import challenge_out
from infrastructure.auth.twofactor.rest.schemas import (
    CodeIn,
    EnrolledFactor,
    FactorListOut,
    PhoneIn,
    RecoveryCodesOut,
    TicketCodeIn,
    TotpEnrollOut,
)
from infrastructure.auth.twofactor.services import twofactor_service

router = Router()


@router.get(
    "/methods",
    response=FactorListOut,
    auth=api_auth,
    summary="List the second factors on this account",
)
def methods(request: HttpRequest) -> FactorListOut:
    factors = twofactor_service.factors(request.user)
    return FactorListOut(
        methods=[
            EnrolledFactor(
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


@router.post(
    "/totp/enroll",
    response={200: TotpEnrollOut, 404: MessageOut, 409: MessageOut},
    auth=api_auth,
    summary="Start authenticator-app enrolment",
)
def totp_enroll(request: HttpRequest) -> TotpEnrollOut:
    """Hand back a fresh secret. It counts for nothing until a code confirms it."""
    enrolment = twofactor_service.start_totp(request.user)
    return TotpEnrollOut(secret=enrolment.secret, otpauth_uri=enrolment.otpauth_uri)


@router.post(
    "/totp/confirm",
    response={200: MessageOut, 400: MessageOut, 404: MessageOut},
    auth=api_auth,
    summary="Confirm authenticator-app enrolment",
)
def totp_confirm(request: HttpRequest, payload: CodeIn) -> MessageOut:
    detail = twofactor_service.confirm_totp(request, request.user, code=payload.code)
    return MessageOut(detail=detail)


@router.post(
    "/sms/enroll",
    response={
        200: ChallengeOut,
        400: MessageOut,
        404: MessageOut,
        409: MessageOut,
        429: MessageOut,
    },
    auth=api_auth,
    summary="Start SMS second-factor enrolment",
)
def sms_enroll(request: HttpRequest, payload: PhoneIn) -> ChallengeOut:
    return challenge_out(twofactor_service.start_sms(request.user, phone=payload.phone))


@router.post(
    "/sms/confirm",
    response={200: MessageOut, 400: MessageOut, 404: MessageOut, 410: MessageOut, 429: MessageOut},
    auth=api_auth,
    summary="Confirm SMS second-factor enrolment",
)
def sms_confirm(request: HttpRequest, payload: TicketCodeIn) -> MessageOut:
    detail = twofactor_service.confirm_sms(
        request, request.user, ticket=payload.ticket, code=payload.code
    )
    return MessageOut(detail=detail)


@router.post(
    "/email/enroll",
    response={200: ChallengeOut, 400: MessageOut, 404: MessageOut, 429: MessageOut},
    auth=api_auth,
    summary="Start email second-factor enrolment",
)
def email_enroll(request: HttpRequest) -> ChallengeOut:
    return challenge_out(twofactor_service.start_email(request.user))


@router.post(
    "/email/confirm",
    response={200: MessageOut, 400: MessageOut, 404: MessageOut, 410: MessageOut, 429: MessageOut},
    auth=api_auth,
    summary="Confirm email second-factor enrolment",
)
def email_confirm(request: HttpRequest, payload: TicketCodeIn) -> MessageOut:
    detail = twofactor_service.confirm_email(
        request, request.user, ticket=payload.ticket, code=payload.code
    )
    return MessageOut(detail=detail)


@router.post(
    "/recovery/generate",
    response={200: RecoveryCodesOut, 404: MessageOut},
    auth=api_auth,
    summary="Replace the recovery codes on this account",
)
def recovery_generate(request: HttpRequest) -> RecoveryCodesOut:
    """Issue a new set. Any codes handed out earlier stop working immediately."""
    return RecoveryCodesOut(codes=twofactor_service.generate_recovery_codes(request, request.user))


@router.delete(
    "/{method}",
    response={200: MessageOut, 400: MessageOut, 404: MessageOut},
    auth=api_auth,
    summary="Turn off a second factor",
)
def remove(request: HttpRequest, method: str) -> MessageOut:
    return MessageOut(detail=twofactor_service.remove(request, request.user, method=method))
