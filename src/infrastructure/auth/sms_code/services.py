"""Everything SMS-code login decides, independent of who asked.

Reaching a number proves control of it, so a successful verification is also
what marks the number verified -- including one that was attached to the account
earlier but never confirmed.
"""

from dataclasses import dataclass

from django.conf import settings
from django.http import HttpRequest
from django.utils import timezone

from infrastructure.auth.core.challenges import get_challenge_store
from infrastructure.auth.core.errors import AuthError
from infrastructure.auth.core.flows import (
    LoginResult,
    complete_login,
    record_event,
    send_code_challenge,
)
from infrastructure.auth.core.identities import (
    create_user_for_phone,
    normalize_phone,
    user_by_phone,
)
from infrastructure.auth.core.models import AuthEventType, PhoneNumber
from infrastructure.auth.core.services import masking_service
from infrastructure.auth.core.sessions import revoke_credentials
from infrastructure.common.responses import ResponseTitle

METHOD = "sms_code"
SIGNUP_PURPOSE = "sms_code_signup"
LOGIN_PURPOSE = "sms_code_login"


@dataclass(frozen=True, slots=True)
class Challenge:
    """Where a code went, and the handle for redeeming it."""

    ticket: str
    channel: str
    destination: str
    expires_in: int


class SmsCodeService:
    """Sign up and sign in with a one-time code sent to a phone number."""

    def start_signup(self, request: HttpRequest, *, phone: str) -> Challenge:
        return self._start(request, phone, SIGNUP_PURPOSE, "Your sign-up code")

    def start_login(self, request: HttpRequest, *, phone: str) -> Challenge:
        return self._start(request, phone, LOGIN_PURPOSE, "Your sign-in code")

    def verify_signup(self, request: HttpRequest, *, ticket: str, code: str) -> LoginResult:
        challenge = get_challenge_store().verify(ticket, code, purpose=SIGNUP_PURPOSE)
        phone = challenge.destination
        if user_by_phone(phone, verified_only=False) is not None:
            raise AuthError(
                "That number already has an account. Sign in instead.",
                status=409,
                title=ResponseTitle.ACCOUNT_EXISTS,
            )
        user = create_user_for_phone(phone)
        record_event(request, AuthEventType.SIGNUP, user=user, method=METHOD, identifier=phone)
        return complete_login(request, user, method=METHOD, identifier=phone)

    def verify_login(self, request: HttpRequest, *, ticket: str, code: str) -> LoginResult:
        challenge = get_challenge_store().verify(ticket, code, purpose=LOGIN_PURPOSE)
        phone = challenge.destination
        user = user_by_phone(phone, verified_only=False)
        if user is None:
            if not settings.AUTH_AUTO_CREATE_USERS:
                raise AuthError(
                    "No account uses that number.",
                    status=404,
                    title=ResponseTitle.ACCOUNT_NOT_FOUND,
                )
            user = create_user_for_phone(phone)
            record_event(request, AuthEventType.SIGNUP, user=user, method=METHOD, identifier=phone)
        else:
            self.confirm_number(phone)
        return complete_login(request, user, method=METHOD, identifier=phone)

    def logout(self, request: HttpRequest, *, token: str = "") -> str:
        if revoke_credentials(request, token):
            record_event(request, AuthEventType.LOGOUT, method=METHOD)
        return "Signed out."

    def confirm_number(self, phone: str) -> None:
        """Mark the number verified, now that a code sent to it has come back.

        No row needs creating here: the account was resolved through this very
        row, so reaching the number only ever promotes a pending one to verified.
        """
        PhoneNumber.objects.filter(number=phone, is_verified=False).update(
            is_verified=True, verified_at=timezone.now()
        )

    def _start(self, request: HttpRequest, raw_phone: str, purpose: str, intro: str) -> Challenge:
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
        return Challenge(
            ticket=ticket,
            channel="sms",
            destination=masking_service.destination("sms", phone),
            expires_in=settings.AUTH_CHALLENGE_TTL_SECONDS,
        )


sms_code_service = SmsCodeService()
