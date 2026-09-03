"""Ask for a link, redeem one, and sign out.

Every decision here is :class:`MagicLinkService`'s.
"""

from django.http import HttpRequest
from ninja import Router

from infrastructure.auth.core.rest.schemas import LoginOut, MessageOut, login_out
from infrastructure.auth.magic_link.rest.schemas import LogoutIn, StartIn, StartOut, VerifyIn
from infrastructure.auth.magic_link.services import LinkSent, magic_link_service

router = Router()


def start_out(sent: LinkSent) -> StartOut:
    return StartOut(detail=sent.detail, destination=sent.destination, expires_in=sent.expires_in)


@router.post(
    "/signup/start",
    response={200: StartOut, 400: MessageOut, 429: MessageOut},
    auth=None,
    summary="Email a sign-up link",
)
def signup_start(request: HttpRequest, payload: StartIn) -> StartOut:
    return start_out(magic_link_service.start_signup(request, email=payload.email))


@router.post(
    "/login/start",
    response={200: StartOut, 400: MessageOut, 429: MessageOut},
    auth=None,
    summary="Email a sign-in link",
)
def login_start(request: HttpRequest, payload: StartIn) -> StartOut:
    return start_out(magic_link_service.start_login(request, email=payload.email))


@router.post(
    "/verify",
    response={200: LoginOut, 400: MessageOut, 403: MessageOut, 404: MessageOut, 410: MessageOut},
    auth=None,
    summary="Sign in with a link token",
)
def verify(request: HttpRequest, payload: VerifyIn) -> LoginOut:
    return login_out(magic_link_service.verify(request, token=payload.token))


@router.post("/logout", response=MessageOut, auth=None, summary="Sign out")
def logout(request: HttpRequest, payload: LogoutIn) -> MessageOut:
    return MessageOut(detail=magic_link_service.logout(request, token=payload.token))
