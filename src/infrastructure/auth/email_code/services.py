"""Everything email-code login decides, independent of who asked.

Both flows send a code to whatever address was supplied and hand back a ticket,
whether or not an account exists. Answering identically is the point: the ticket
is worthless without the code, so nothing is given away, and the question of
"does this address have an account" is only settled once the caller has proved
they can read its mail.
"""

from dataclasses import dataclass

from django.conf import settings
from django.http import HttpRequest

from infrastructure.accounts.profiles import confirm_email
from infrastructure.auth.core.challenges import get_challenge_store
from infrastructure.auth.core.errors import AuthError
from infrastructure.auth.core.flows import (
    LoginResult,
    complete_login,
    record_event,
    send_code_challenge,
)
from infrastructure.auth.core.identities import (
    create_user_for_email,
    normalize_email,
    user_by_email,
)
from infrastructure.auth.core.models import AuthEventType
from infrastructure.auth.core.services import masking_service
from infrastructure.auth.core.sessions import revoke_credentials
from infrastructure.common.responses import ResponseTitle

METHOD = "email_code"
SIGNUP_PURPOSE = "email_code_signup"
LOGIN_PURPOSE = "email_code_login"


@dataclass(frozen=True, slots=True)
class Challenge:
    """Where a code went, and the handle for redeeming it."""

    ticket: str
    channel: str
    destination: str
    expires_in: int


class EmailCodeService:
    """Sign up and sign in with a one-time code sent to an email address."""

    def start_signup(self, request: HttpRequest, *, email: str) -> Challenge:
        return self._start(request, email, SIGNUP_PURPOSE, "Your sign-up code")

    def start_login(self, request: HttpRequest, *, email: str) -> Challenge:
        return self._start(request, email, LOGIN_PURPOSE, "Your sign-in code")

    def verify_signup(self, request: HttpRequest, *, ticket: str, code: str) -> LoginResult:
        challenge = get_challenge_store().verify(ticket, code, purpose=SIGNUP_PURPOSE)
        email = challenge.destination
        if user_by_email(email) is not None:
            raise AuthError(
                "That address already has an account. Sign in instead.",
                status=409,
                title=ResponseTitle.ACCOUNT_EXISTS,
            )
        user = create_user_for_email(email)
        record_event(request, AuthEventType.SIGNUP, user=user, method=METHOD, identifier=email)
        confirm_email(user, email)
        return complete_login(request, user, method=METHOD, identifier=email)

    def verify_login(self, request: HttpRequest, *, ticket: str, code: str) -> LoginResult:
        challenge = get_challenge_store().verify(ticket, code, purpose=LOGIN_PURPOSE)
        email = challenge.destination
        user = user_by_email(email)
        if user is None:
            if not settings.AUTH_AUTO_CREATE_USERS:
                raise AuthError(
                    "No account uses that address.",
                    status=404,
                    title=ResponseTitle.ACCOUNT_NOT_FOUND,
                )
            user = create_user_for_email(email)
            record_event(request, AuthEventType.SIGNUP, user=user, method=METHOD, identifier=email)
        confirm_email(user, email)
        return complete_login(request, user, method=METHOD, identifier=email)

    def logout(self, request: HttpRequest, *, token: str = "") -> str:
        if revoke_credentials(request, token):
            record_event(request, AuthEventType.LOGOUT, method=METHOD)
        return "Signed out."

    def _start(self, request: HttpRequest, raw_email: str, purpose: str, intro: str) -> Challenge:
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
        return Challenge(
            ticket=ticket,
            channel="email",
            destination=masking_service.destination("email", email),
            expires_in=settings.AUTH_CHALLENGE_TTL_SECONDS,
        )


email_code_service = EmailCodeService()
