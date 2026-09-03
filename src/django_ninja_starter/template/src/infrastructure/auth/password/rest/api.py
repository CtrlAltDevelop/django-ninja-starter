"""What an account does to its own password once it has one.

Recovery for a caller who cannot sign in, and a change for one who can. The
ways in and out are next door, in ``login.py``.
"""

from django.http import HttpRequest
from ninja import Router

from infrastructure.auth.core.rest.schemas import MessageOut
from infrastructure.auth.core.sessions import api_auth
from infrastructure.auth.password.rest.schemas import ChangeIn, ForgotIn, ForgotOut, ResetIn
from infrastructure.auth.password.services import password_service

router = Router()


@router.post(
    "/forgot",
    response={200: ForgotOut, 400: MessageOut, 429: MessageOut},
    auth=None,
    summary="Request a password reset code",
)
def forgot(request: HttpRequest, payload: ForgotIn) -> ForgotOut:
    """Send a reset code, answering identically for addresses with no account."""
    ticket = password_service.forgot(request, email=payload.email)
    return ForgotOut(detail=ticket.detail, ticket=ticket.ticket, expires_in=ticket.expires_in)


@router.post(
    "/reset",
    response={200: MessageOut, 400: MessageOut, 410: MessageOut, 429: MessageOut},
    auth=None,
    summary="Set a new password with a reset code",
)
def reset(request: HttpRequest, payload: ResetIn) -> MessageOut:
    detail = password_service.reset(
        request, ticket=payload.ticket, code=payload.code, password=payload.password
    )
    return MessageOut(detail=detail)


@router.post(
    "/change",
    response={200: MessageOut, 400: MessageOut},
    auth=api_auth,
    summary="Change the password on this account",
)
def change(request: HttpRequest, payload: ChangeIn) -> MessageOut:
    detail = password_service.change(
        request,
        request.user,
        current_password=payload.current_password,
        new_password=payload.new_password,
    )
    return MessageOut(detail=detail)
