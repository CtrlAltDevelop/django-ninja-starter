"""Sign up and sign in with a one-time code sent to an email address.

Both flows send a code to whatever address was supplied and hand back a ticket,
whether or not an account exists. Answering identically is the point: the ticket
is worthless without the code, so nothing is given away, and the question of
"does this address have an account" is only settled once the caller has proved
they can read its mail.
"""

from django.conf import settings
from django.http import HttpRequest
from ninja import Router

from infrastructure.accounts.profiles import confirm_email
from infrastructure.auth.core.challenges import get_challenge_store
from infrastructure.auth.core.errors import AuthError
from infrastructure.auth.core.flows import complete_login, record_event, send_code_challenge
from infrastructure.auth.core.identities import (
    create_user_for_email,
    normalize_email,
    user_by_email,
)
from infrastructure.auth.core.models import AuthEventType
from infrastructure.auth.core.schemas import ChallengeOut, LoginOut, MessageOut, login_out, mask
from infrastructure.auth.core.sessions import revoke_credentials
from infrastructure.auth.email_code.schemas import LogoutIn, StartIn, VerifyIn
from infrastructure.common.responses import ResponseTitle

router = Router()
METHOD = "email_code"
SIGNUP_PURPOSE = "email_code_signup"
LOGIN_PURPOSE = "email_code_login"


def _start(request: HttpRequest, raw_email: str, purpose: str, intro: str) -> ChallengeOut:
    email = normalize_email(raw_email)
    ticket = send_code_challenge(
        request,
        purpose=purpose,
        subject="",
        channel="email",
        destination=email,
        method=METHOD,
        intro=intro,
    )
    return ChallengeOut(
        ticket=ticket,
        channel="email",
        destination=mask("email", email),
        expires_in=settings.AUTH_CHALLENGE_TTL_SECONDS,
    )


@router.post(
    "/signup/start",
    response={200: ChallengeOut, 400: MessageOut, 429: MessageOut},
    auth=None,
    summary="Send a sign-up code by email",
)
def signup_start(request: HttpRequest, payload: StartIn) -> ChallengeOut:
    return _start(request, payload.email, SIGNUP_PURPOSE, "Your sign-up code")


@router.post(
    "/signup/verify",
    response={200: LoginOut, 400: MessageOut, 403: MessageOut, 409: MessageOut, 410: MessageOut},
    auth=None,
    summary="Create an account with an emailed code",
)
def signup_verify(request: HttpRequest, payload: VerifyIn) -> LoginOut:
    challenge = get_challenge_store().verify(payload.ticket, payload.code, purpose=SIGNUP_PURPOSE)
    email = challenge.destination
    if user_by_email(email) is not None:
        raise AuthError(
            "That address already has an account. Sign in instead.",
            status=409,
            title=ResponseTitle.ACCOUNT_EXISTS,
        )
    user = create_user_for_email(email)
    record_event(
        request,
        AuthEventType.SIGNUP,
        user=user,
        method=METHOD,
        identifier=email,
    )
    confirm_email(user, email)
    return login_out(complete_login(request, user, method=METHOD, identifier=email))


@router.post(
    "/login/start",
    response={200: ChallengeOut, 400: MessageOut, 429: MessageOut},
    auth=None,
    summary="Send a sign-in code by email",
)
def login_start(request: HttpRequest, payload: StartIn) -> ChallengeOut:
    return _start(request, payload.email, LOGIN_PURPOSE, "Your sign-in code")


@router.post(
    "/login/verify",
    response={200: LoginOut, 400: MessageOut, 403: MessageOut, 404: MessageOut, 410: MessageOut},
    auth=None,
    summary="Sign in with an emailed code",
)
def login_verify(request: HttpRequest, payload: VerifyIn) -> LoginOut:
    challenge = get_challenge_store().verify(payload.ticket, payload.code, purpose=LOGIN_PURPOSE)
    email = challenge.destination
    user = user_by_email(email)
    if user is None:
        if not settings.AUTH_AUTO_CREATE_USERS:
            raise AuthError(
                "No account uses that address.", status=404, title=ResponseTitle.ACCOUNT_NOT_FOUND
            )
        user = create_user_for_email(email)
        record_event(
            request,
            AuthEventType.SIGNUP,
            user=user,
            method=METHOD,
            identifier=email,
        )
    confirm_email(user, email)
    return login_out(complete_login(request, user, method=METHOD, identifier=email))


@router.post("/logout", response=MessageOut, auth=None, summary="Sign out")
def logout(request: HttpRequest, payload: LogoutIn) -> MessageOut:
    if revoke_credentials(request, payload.token):
        record_event(request, AuthEventType.LOGOUT, method=METHOD)
    return MessageOut(detail="Signed out.")
