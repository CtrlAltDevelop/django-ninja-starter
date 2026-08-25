"""Password sign-up, sign-in, reset, change, and sign-out."""

from django.conf import settings
from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction
from django.http import HttpRequest
from ninja import Router

from infrastructure.auth.core.challenges import get_challenge_store
from infrastructure.auth.core.errors import AuthError
from infrastructure.auth.core.flows import (
    complete_login,
    decoy_challenge,
    record_event,
    send_code_challenge,
    user_from_challenge,
)
from infrastructure.auth.core.identities import IdentityError, normalize_email, user_by_login
from infrastructure.auth.core.models import AuthEventType
from infrastructure.auth.core.schemas import LoginOut, MessageOut, login_out
from infrastructure.auth.core.sessions import api_auth, revoke_all_for_user, revoke_credentials
from infrastructure.auth.core.throttling import guard_attempts
from infrastructure.auth.password.schemas import (
    ChangeIn,
    ForgotIn,
    ForgotOut,
    LoginIn,
    LogoutIn,
    ResetIn,
    SignupIn,
)
from infrastructure.auth.password.services import (
    LOGIN_ATTEMPT_LIMIT,
    RESET_PURPOSE,
    enforce_password_policy,
    password_matches,
)

router = Router()
METHOD = "password"


@router.post(
    "/signup",
    response={200: LoginOut, 400: MessageOut, 403: MessageOut, 409: MessageOut},
    auth=None,
    summary="Create an account with a password",
)
def signup(request: HttpRequest, payload: SignupIn) -> LoginOut:
    user_model = get_user_model()
    username_field = user_model.USERNAME_FIELD
    identifier = payload.identifier.strip()
    if not identifier:
        raise AuthError("Enter a username.", status=400)
    if username_field == "email":
        identifier = normalize_email(identifier)
    attributes = {username_field: identifier}
    if payload.email and username_field != "email":
        attributes["email"] = normalize_email(payload.email)
    enforce_password_policy(payload.password)
    try:
        with transaction.atomic():
            user = user_model._default_manager.create_user(
                **attributes,
                password=payload.password,
            )
    except IntegrityError as error:
        raise AuthError("That account already exists.", status=409) from error
    record_event(
        request,
        AuthEventType.SIGNUP,
        user=user,
        method=METHOD,
        identifier=identifier,
    )
    return login_out(complete_login(request, user, method=METHOD, identifier=identifier))


@router.post(
    "/login",
    response={200: LoginOut, 400: MessageOut, 401: MessageOut, 403: MessageOut, 429: MessageOut},
    auth=None,
    summary="Sign in with a password",
)
def login(request: HttpRequest, payload: LoginIn) -> LoginOut:
    identifier = payload.identifier.strip()
    guard_attempts(f"{METHOD}:login", identifier.lower(), limit=LOGIN_ATTEMPT_LIMIT)
    try:
        user = user_by_login(identifier)
    except IdentityError:
        user = None
    if not password_matches(user, payload.password):
        record_event(
            request,
            AuthEventType.LOGIN_FAILED,
            user=user,
            method=METHOD,
            identifier=identifier,
        )
        raise AuthError("Those credentials are not valid.", status=401)
    return login_out(complete_login(request, user, method=METHOD, identifier=identifier))


@router.post(
    "/logout",
    response=MessageOut,
    auth=None,
    summary="Sign out",
)
def logout(request: HttpRequest, payload: LogoutIn) -> MessageOut:
    """Retire the presented credential. Absent or stale tokens still read as success."""
    revoked = revoke_credentials(request, payload.token)
    if revoked:
        record_event(request, AuthEventType.LOGOUT, method=METHOD)
    return MessageOut(detail="Signed out.")


@router.post(
    "/forgot",
    response={200: ForgotOut, 400: MessageOut, 429: MessageOut},
    auth=None,
    summary="Request a password reset code",
)
def forgot(request: HttpRequest, payload: ForgotIn) -> ForgotOut:
    """Send a reset code, answering identically for addresses with no account."""
    from infrastructure.auth.core.identities import user_by_email

    email = normalize_email(payload.email)
    try:
        user = user_by_email(email)
    except IdentityError:
        user = None
    if user is None:
        ticket = decoy_challenge(RESET_PURPOSE, channel="email", destination=email)
    else:
        ticket = send_code_challenge(
            request,
            purpose=RESET_PURPOSE,
            subject=str(user.pk),
            channel="email",
            destination=email,
            method=METHOD,
            intro="Your password reset code",
        )
        record_event(
            request,
            AuthEventType.PASSWORD_RESET_REQUESTED,
            user=user,
            method=METHOD,
            identifier=email,
        )
    return ForgotOut(
        detail="If that address has an account, a reset code is on its way.",
        ticket=ticket,
        expires_in=settings.AUTH_CHALLENGE_TTL_SECONDS,
    )


@router.post(
    "/reset",
    response={200: MessageOut, 400: MessageOut, 410: MessageOut, 429: MessageOut},
    auth=None,
    summary="Set a new password with a reset code",
)
def reset(request: HttpRequest, payload: ResetIn) -> MessageOut:
    challenge = get_challenge_store().verify(payload.ticket, payload.code, purpose=RESET_PURPOSE)
    user = user_from_challenge(challenge)
    enforce_password_policy(payload.password, user)
    user.set_password(payload.password)
    user.save(update_fields=["password"])
    revoked = revoke_all_for_user(user)
    record_event(
        request,
        AuthEventType.PASSWORD_CHANGED,
        user=user,
        method=METHOD,
        credentials_revoked=revoked,
        via="reset",
    )
    return MessageOut(detail="Password updated. Sign in with your new password.")


@router.post(
    "/change",
    response={200: MessageOut, 400: MessageOut, 401: MessageOut},
    auth=api_auth,
    summary="Change the password on this account",
)
def change(request: HttpRequest, payload: ChangeIn) -> MessageOut:
    """Change a password and retire every credential issued under the old one."""
    if not password_matches(request.user, payload.current_password):
        raise AuthError("Those credentials are not valid.", status=401)
    enforce_password_policy(payload.new_password, request.user)
    request.user.set_password(payload.new_password)
    request.user.save(update_fields=["password"])
    revoked = revoke_all_for_user(request.user)
    record_event(
        request,
        AuthEventType.PASSWORD_CHANGED,
        user=request.user,
        method=METHOD,
        credentials_revoked=revoked,
        via="change",
    )
    return MessageOut(detail="Password updated. Sign in again.")
