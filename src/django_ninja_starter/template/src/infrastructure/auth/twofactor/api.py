"""Second-factor endpoints: the login-time challenge and enrolment management."""

from typing import Any

from django.conf import settings
from django.http import HttpRequest
from ninja import Router

from infrastructure.auth.core.challenges import InvalidCode, get_challenge_store
from infrastructure.auth.core.errors import AuthError
from infrastructure.auth.core.flows import (
    PENDING_PURPOSE,
    pending_methods,
    record_event,
    resolve_pending_login,
)
from infrastructure.auth.core.models import AuthEventType, PhoneNumber
from infrastructure.auth.core.schemas import (
    ChallengeOut,
    LoginOut,
    MessageOut,
    credentials_out,
    mask,
)
from infrastructure.auth.core.sessions import api_auth, issue_credentials
from infrastructure.auth.twofactor.models import SecondFactor, SecondFactorMethod
from infrastructure.auth.twofactor.schemas import (
    ChallengeIn,
    CodeIn,
    EnrolledFactor,
    FactorListOut,
    PhoneIn,
    RecoveryCodesOut,
    TicketCodeIn,
    TotpEnrollOut,
    VerifyIn,
)
from infrastructure.auth.twofactor.services import (
    begin_totp,
    consume_recovery_code,
    enroll_email,
    enroll_sms,
    factor_for,
    issue_recovery_codes,
    redeem_factor_code,
    require_enabled,
    send_factor_code,
    unused_recovery_codes,
    verify_totp,
)

router = Router()

INFERABLE = (SecondFactorMethod.TOTP, SecondFactorMethod.SMS, SecondFactorMethod.EMAIL)


def _chosen_method(metadata: dict[str, Any], methods: list[str], requested: str) -> str:
    """Work out which factor the caller means, without ever guessing recovery.

    Spending a recovery code on what was meant to be a mistyped authenticator
    code would burn a credential the user may have printed out months ago, so
    recovery is only ever used when asked for by name.
    """
    if requested:
        return requested
    if metadata.get("code_ticket") and metadata.get("code_method"):
        return str(metadata["code_method"])
    candidates = [method for method in methods if method in INFERABLE]
    if SecondFactorMethod.TOTP in candidates:
        return SecondFactorMethod.TOTP
    if len(candidates) == 1:
        return candidates[0]
    raise AuthError("Specify which second factor you are using.", status=400)


@router.post(
    "/challenge",
    response={200: ChallengeOut, 400: MessageOut, 410: MessageOut, 429: MessageOut},
    auth=None,
    summary="Send a code for a pending sign-in",
)
def challenge(request: HttpRequest, payload: ChallengeIn) -> ChallengeOut:
    """Deliver an SMS or email code for a login that is waiting on a second factor."""
    user, metadata = resolve_pending_login(payload.login_ticket)
    if payload.method not in pending_methods(metadata):
        raise AuthError("That second factor is not set up for this account.", status=400)
    factor = factor_for(user, payload.method)
    ticket, channel, destination = send_factor_code(user, factor)
    get_challenge_store().update_metadata(
        payload.login_ticket,
        purpose=PENDING_PURPOSE,
        metadata={**metadata, "code_ticket": ticket, "code_method": payload.method},
    )
    record_event(
        request,
        AuthEventType.CODE_SENT,
        user=user,
        method=payload.method,
        identifier=destination,
        stage="second_factor",
    )
    return ChallengeOut(
        ticket=payload.login_ticket,
        channel=channel,
        destination=mask(channel, destination),
        expires_in=settings.AUTH_CHALLENGE_TTL_SECONDS,
    )


@router.post(
    "/verify",
    response={200: LoginOut, 400: MessageOut, 403: MessageOut, 410: MessageOut, 429: MessageOut},
    auth=None,
    summary="Finish a sign-in with a second factor",
)
def verify(request: HttpRequest, payload: VerifyIn) -> LoginOut:
    """Check the second factor and, if it holds, issue the credential."""
    store = get_challenge_store()
    user, metadata = resolve_pending_login(payload.login_ticket)
    methods = pending_methods(metadata)
    method = _chosen_method(metadata, methods, payload.method)
    if method not in methods:
        raise AuthError("That second factor is not set up for this account.", status=400)

    code_ticket = ""
    if method == SecondFactorMethod.TOTP:
        accepted = verify_totp(factor_for(user, method), payload.code)
    elif method == SecondFactorMethod.RECOVERY:
        accepted = consume_recovery_code(user, payload.code)
    else:
        code_ticket = str(metadata.get("code_ticket", ""))
        if not code_ticket or metadata.get("code_method") != method:
            raise AuthError("Request a code before verifying it.", status=400)
        try:
            redeem_factor_code(code_ticket, payload.code, user)
        except InvalidCode:
            accepted = False
        else:
            accepted = True
            factor_for(user, method).mark_used()

    if not accepted:
        store.fail(payload.login_ticket)
        record_event(
            request,
            AuthEventType.SECOND_FACTOR_FAILED,
            user=user,
            method=method,
        )
        raise AuthError("That code is not valid.", status=400)

    store.discard(payload.login_ticket)
    if code_ticket:
        store.discard(code_ticket)
    first_factor = str(metadata.get("first_factor", ""))
    record_event(
        request,
        AuthEventType.SECOND_FACTOR_SUCCEEDED,
        user=user,
        method=method,
    )
    credentials = issue_credentials(request, user, method=f"{first_factor}+{method}")
    record_event(
        request,
        AuthEventType.LOGIN_SUCCEEDED,
        user=user,
        method=first_factor,
        second_factor=method,
    )
    return LoginOut(requires_second_factor=False, credentials=credentials_out(credentials))


@router.get(
    "/methods",
    response=FactorListOut,
    auth=api_auth,
    summary="List the second factors on this account",
)
def methods(request: HttpRequest) -> FactorListOut:
    factors = SecondFactor.objects.filter(user=request.user)
    return FactorListOut(
        methods=[
            EnrolledFactor(
                method=factor.method,
                destination=mask(
                    "sms" if factor.method == SecondFactorMethod.SMS else "email",
                    factor.destination,
                )
                if factor.destination
                else "",
                confirmed=factor.is_confirmed,
                last_used_at=factor.last_used_at.isoformat() if factor.last_used_at else "",
            )
            for factor in factors
        ],
        available=list(settings.AUTH_SECOND_FACTORS),
        unused_recovery_codes=unused_recovery_codes(request.user),
    )


@router.post(
    "/totp/enroll",
    response={200: TotpEnrollOut, 404: MessageOut, 409: MessageOut},
    auth=api_auth,
    summary="Start authenticator-app enrolment",
)
def totp_enroll(request: HttpRequest) -> TotpEnrollOut:
    """Hand back a fresh secret. It counts for nothing until a code confirms it."""
    _, secret, uri = begin_totp(request.user)
    return TotpEnrollOut(secret=secret, otpauth_uri=uri)


@router.post(
    "/totp/confirm",
    response={200: MessageOut, 400: MessageOut, 404: MessageOut},
    auth=api_auth,
    summary="Confirm authenticator-app enrolment",
)
def totp_confirm(request: HttpRequest, payload: CodeIn) -> MessageOut:
    require_enabled(SecondFactorMethod.TOTP)
    factor = SecondFactor.objects.filter(user=request.user, method=SecondFactorMethod.TOTP).first()
    if factor is None:
        raise AuthError("Start authenticator enrolment first.", status=400)
    if not verify_totp(factor, payload.code):
        raise AuthError("That code is not valid.", status=400)
    factor.confirm()
    record_event(
        request,
        AuthEventType.SECOND_FACTOR_ENROLLED,
        user=request.user,
        method=SecondFactorMethod.TOTP,
    )
    return MessageOut(detail="Authenticator app enabled.")


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
    factor = enroll_sms(request.user, payload.phone)
    ticket, channel, destination = send_factor_code(request.user, factor)
    return ChallengeOut(
        ticket=ticket,
        channel=channel,
        destination=mask(channel, destination),
        expires_in=settings.AUTH_CHALLENGE_TTL_SECONDS,
    )


@router.post(
    "/sms/confirm",
    response={200: MessageOut, 400: MessageOut, 404: MessageOut, 410: MessageOut, 429: MessageOut},
    auth=api_auth,
    summary="Confirm SMS second-factor enrolment",
)
def sms_confirm(request: HttpRequest, payload: TicketCodeIn) -> MessageOut:
    require_enabled(SecondFactorMethod.SMS)
    challenge = redeem_factor_code(payload.ticket, payload.code, request.user)
    factor = SecondFactor.objects.filter(user=request.user, method=SecondFactorMethod.SMS).first()
    if factor is None:
        raise AuthError("Start SMS enrolment first.", status=400)
    factor.confirm()
    phone = PhoneNumber.objects.filter(user=request.user, number=challenge.destination).first()
    if phone is not None and not phone.is_verified:
        phone.mark_verified()
    record_event(
        request,
        AuthEventType.SECOND_FACTOR_ENROLLED,
        user=request.user,
        method=SecondFactorMethod.SMS,
    )
    return MessageOut(detail="SMS codes enabled.")


@router.post(
    "/email/enroll",
    response={200: ChallengeOut, 400: MessageOut, 404: MessageOut, 429: MessageOut},
    auth=api_auth,
    summary="Start email second-factor enrolment",
)
def email_enroll(request: HttpRequest) -> ChallengeOut:
    factor = enroll_email(request.user)
    ticket, channel, destination = send_factor_code(request.user, factor)
    return ChallengeOut(
        ticket=ticket,
        channel=channel,
        destination=mask(channel, destination),
        expires_in=settings.AUTH_CHALLENGE_TTL_SECONDS,
    )


@router.post(
    "/email/confirm",
    response={200: MessageOut, 400: MessageOut, 404: MessageOut, 410: MessageOut, 429: MessageOut},
    auth=api_auth,
    summary="Confirm email second-factor enrolment",
)
def email_confirm(request: HttpRequest, payload: TicketCodeIn) -> MessageOut:
    require_enabled(SecondFactorMethod.EMAIL)
    redeem_factor_code(payload.ticket, payload.code, request.user)
    factor = SecondFactor.objects.filter(user=request.user, method=SecondFactorMethod.EMAIL).first()
    if factor is None:
        raise AuthError("Start email enrolment first.", status=400)
    factor.confirm()
    record_event(
        request,
        AuthEventType.SECOND_FACTOR_ENROLLED,
        user=request.user,
        method=SecondFactorMethod.EMAIL,
    )
    return MessageOut(detail="Email codes enabled.")


@router.post(
    "/recovery/generate",
    response={200: RecoveryCodesOut, 404: MessageOut},
    auth=api_auth,
    summary="Replace the recovery codes on this account",
)
def recovery_generate(request: HttpRequest) -> RecoveryCodesOut:
    """Issue a new set. Any codes handed out earlier stop working immediately."""
    codes = issue_recovery_codes(request.user)
    record_event(
        request,
        AuthEventType.SECOND_FACTOR_ENROLLED,
        user=request.user,
        method=SecondFactorMethod.RECOVERY,
    )
    return RecoveryCodesOut(codes=codes)


@router.delete(
    "/{method}",
    response={200: MessageOut, 400: MessageOut, 404: MessageOut},
    auth=api_auth,
    summary="Turn off a second factor",
)
def remove(request: HttpRequest, method: str) -> MessageOut:
    factor = SecondFactor.objects.filter(user=request.user, method=method).first()
    if factor is None:
        raise AuthError("That second factor is not set up for this account.", status=404)
    factor.delete()
    if method == SecondFactorMethod.RECOVERY:
        request.user.auth_recovery_codes.all().delete()
    record_event(
        request,
        AuthEventType.SECOND_FACTOR_REMOVED,
        user=request.user,
        method=method,
    )
    return MessageOut(detail=f"{method} second factor removed.")
