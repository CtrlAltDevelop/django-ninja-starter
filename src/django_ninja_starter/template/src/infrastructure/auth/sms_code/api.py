"""Sign up and sign in with a one-time code sent to a phone number.

Reaching a number proves control of it, so a successful verification is also
what marks the number verified -- including one that was attached to the account
earlier but never confirmed.
"""

from django.conf import settings
from django.http import HttpRequest
from django.utils import timezone
from ninja import Router

from infrastructure.auth.core.challenges import get_challenge_store
from infrastructure.auth.core.errors import AuthError
from infrastructure.auth.core.flows import complete_login, record_event, send_code_challenge
from infrastructure.auth.core.identities import (
    create_user_for_phone,
    normalize_phone,
    user_by_phone,
)
from infrastructure.auth.core.models import AuthEventType, PhoneNumber
from infrastructure.auth.core.schemas import ChallengeOut, LoginOut, MessageOut, login_out, mask
from infrastructure.auth.core.sessions import revoke_credentials
from infrastructure.auth.sms_code.schemas import LogoutIn, StartIn, VerifyIn

router = Router()
METHOD = "sms_code"
SIGNUP_PURPOSE = "sms_code_signup"
LOGIN_PURPOSE = "sms_code_login"


def _start(request: HttpRequest, raw_phone: str, purpose: str, intro: str) -> ChallengeOut:
    phone = normalize_phone(raw_phone)
    ticket = send_code_challenge(
        request,
        purpose=purpose,
        subject="",
        channel="sms",
        destination=phone,
        method=METHOD,
        intro=intro,
    )
    return ChallengeOut(
        ticket=ticket,
        channel="sms",
        destination=mask("sms", phone),
        expires_in=settings.AUTH_CHALLENGE_TTL_SECONDS,
    )


def _confirm_number(phone: str) -> None:
    """Mark the number verified, now that a code sent to it has come back.

    No row needs creating here: the account was resolved through this very row,
    so reaching the number only ever promotes a pending one to verified.
    """
    PhoneNumber.objects.filter(number=phone, is_verified=False).update(
        is_verified=True, verified_at=timezone.now()
    )


@router.post(
    "/signup/start",
    response={200: ChallengeOut, 400: MessageOut, 429: MessageOut},
    auth=None,
    summary="Send a sign-up code by SMS",
)
def signup_start(request: HttpRequest, payload: StartIn) -> ChallengeOut:
    return _start(request, payload.phone, SIGNUP_PURPOSE, "Your sign-up code")


@router.post(
    "/signup/verify",
    response={200: LoginOut, 400: MessageOut, 403: MessageOut, 409: MessageOut, 410: MessageOut},
    auth=None,
    summary="Create an account with an SMS code",
)
def signup_verify(request: HttpRequest, payload: VerifyIn) -> LoginOut:
    challenge = get_challenge_store().verify(payload.ticket, payload.code, purpose=SIGNUP_PURPOSE)
    phone = challenge.destination
    if user_by_phone(phone, verified_only=False) is not None:
        raise AuthError("That number already has an account. Sign in instead.", status=409)
    user = create_user_for_phone(phone)
    record_event(
        request,
        AuthEventType.SIGNUP,
        user=user,
        method=METHOD,
        identifier=phone,
    )
    return login_out(complete_login(request, user, method=METHOD, identifier=phone))


@router.post(
    "/login/start",
    response={200: ChallengeOut, 400: MessageOut, 429: MessageOut},
    auth=None,
    summary="Send a sign-in code by SMS",
)
def login_start(request: HttpRequest, payload: StartIn) -> ChallengeOut:
    return _start(request, payload.phone, LOGIN_PURPOSE, "Your sign-in code")


@router.post(
    "/login/verify",
    response={200: LoginOut, 400: MessageOut, 403: MessageOut, 404: MessageOut, 410: MessageOut},
    auth=None,
    summary="Sign in with an SMS code",
)
def login_verify(request: HttpRequest, payload: VerifyIn) -> LoginOut:
    challenge = get_challenge_store().verify(payload.ticket, payload.code, purpose=LOGIN_PURPOSE)
    phone = challenge.destination
    user = user_by_phone(phone, verified_only=False)
    if user is None:
        if not settings.AUTH_AUTO_CREATE_USERS:
            raise AuthError("No account uses that number.", status=404)
        user = create_user_for_phone(phone)
        record_event(
            request,
            AuthEventType.SIGNUP,
            user=user,
            method=METHOD,
            identifier=phone,
        )
    else:
        _confirm_number(phone)
    return login_out(complete_login(request, user, method=METHOD, identifier=phone))


@router.post("/logout", response=MessageOut, auth=None, summary="Sign out")
def logout(request: HttpRequest, payload: LogoutIn) -> MessageOut:
    if revoke_credentials(request, payload.token):
        record_event(request, AuthEventType.LOGOUT, method=METHOD)
    return MessageOut(detail="Signed out.")
