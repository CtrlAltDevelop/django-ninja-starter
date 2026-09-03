"""Sign up, sign in and sign out with a code sent to an email address.

Every decision here is :class:`EmailCodeService`'s -- including the one that
matters most, which is that the two `start` routes answer identically whether or
not the address has an account.
"""

from django.http import HttpRequest
from ninja import Router

from infrastructure.auth.core.rest.schemas import ChallengeOut, LoginOut, MessageOut, login_out
from infrastructure.auth.email_code.rest.schemas import LogoutIn, StartIn, VerifyIn
from infrastructure.auth.email_code.services import Challenge, email_code_service

router = Router()


def challenge_out(challenge: Challenge) -> ChallengeOut:
    return ChallengeOut(
        ticket=challenge.ticket,
        channel=challenge.channel,
        destination=challenge.destination,
        expires_in=challenge.expires_in,
    )


@router.post(
    "/signup/start",
    response={200: ChallengeOut, 400: MessageOut, 429: MessageOut},
    auth=None,
    summary="Send a sign-up code by email",
)
def signup_start(request: HttpRequest, payload: StartIn) -> ChallengeOut:
    return challenge_out(email_code_service.start_signup(request, email=payload.email))


@router.post(
    "/signup/verify",
    response={200: LoginOut, 400: MessageOut, 403: MessageOut, 409: MessageOut, 410: MessageOut},
    auth=None,
    summary="Create an account with an emailed code",
)
def signup_verify(request: HttpRequest, payload: VerifyIn) -> LoginOut:
    return login_out(
        email_code_service.verify_signup(request, ticket=payload.ticket, code=payload.code)
    )


@router.post(
    "/login/start",
    response={200: ChallengeOut, 400: MessageOut, 429: MessageOut},
    auth=None,
    summary="Send a sign-in code by email",
)
def login_start(request: HttpRequest, payload: StartIn) -> ChallengeOut:
    return challenge_out(email_code_service.start_login(request, email=payload.email))


@router.post(
    "/login/verify",
    response={200: LoginOut, 400: MessageOut, 403: MessageOut, 404: MessageOut, 410: MessageOut},
    auth=None,
    summary="Sign in with an emailed code",
)
def login_verify(request: HttpRequest, payload: VerifyIn) -> LoginOut:
    return login_out(
        email_code_service.verify_login(request, ticket=payload.ticket, code=payload.code)
    )


@router.post("/logout", response=MessageOut, auth=None, summary="Sign out")
def logout(request: HttpRequest, payload: LogoutIn) -> MessageOut:
    return MessageOut(detail=email_code_service.logout(request, token=payload.token))
