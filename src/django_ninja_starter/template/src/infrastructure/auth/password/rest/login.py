"""The ways in, and the way out.

Split from the rest of the app's endpoints because these three are the only ones
that hand out or retire a credential -- everything else under this prefix
assumes the caller already has one. Reading the login flow should not mean
reading past the password-management routes to find it.

Every decision here is :class:`PasswordService`'s.
"""

from django.http import HttpRequest
from ninja import Router

from infrastructure.auth.core.rest.schemas import LoginOut, MessageOut, login_out
from infrastructure.auth.password.rest.schemas import LoginIn, LogoutIn, SignupIn
from infrastructure.auth.password.services import password_service

router = Router()


@router.post(
    "/signup",
    response={200: LoginOut, 400: MessageOut, 403: MessageOut, 409: MessageOut},
    auth=None,
    summary="Create an account with a password",
)
def signup(request: HttpRequest, payload: SignupIn) -> LoginOut:
    return login_out(
        password_service.signup(
            request,
            identifier=payload.identifier,
            password=payload.password,
            email=payload.email,
        )
    )


@router.post(
    "/login",
    response={200: LoginOut, 400: MessageOut, 401: MessageOut, 403: MessageOut, 429: MessageOut},
    auth=None,
    summary="Sign in with a password",
)
def login(request: HttpRequest, payload: LoginIn) -> LoginOut:
    return login_out(
        password_service.login(request, identifier=payload.identifier, password=payload.password)
    )


@router.post(
    "/logout",
    response=MessageOut,
    auth=None,
    summary="Sign out",
)
def logout(request: HttpRequest, payload: LogoutIn) -> MessageOut:
    """Retire the presented credential. Absent or stale tokens still read as success."""
    return MessageOut(detail=password_service.logout(request, token=payload.token))
