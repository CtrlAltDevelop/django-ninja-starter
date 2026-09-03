"""Everything password login decides, independent of who asked.

The router used to hold this, which meant a second transport would have had to
copy the timing defence, the reset-code purpose and the "a wrong current
password is a 400, not a 401" rule along with it. All three now have one home,
and the three transport packages beside this file only frame what it returns.

Two of the methods take the request. Not for its body -- the arguments carry
that -- but because signing somebody in records where from, and a credential is
issued against the caller's address and user agent. django-socio-grpc's
stand-in request answers those the same way Django's does, which is what lets a
gRPC login be the same login.
"""

from dataclasses import dataclass
from typing import Any

from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.http import HttpRequest

from infrastructure.auth.core.challenges import get_challenge_store
from infrastructure.auth.core.errors import AuthError
from infrastructure.auth.core.flows import (
    LoginResult,
    complete_login,
    decoy_challenge,
    record_event,
    send_code_challenge,
    user_from_challenge,
)
from infrastructure.auth.core.identities import (
    IdentityError,
    normalize_email,
    user_by_email,
    user_by_login,
)
from infrastructure.auth.core.models import AuthEventType
from infrastructure.auth.core.sessions import revoke_all_for_user, revoke_credentials
from infrastructure.auth.core.throttling import guard_attempts
from infrastructure.common.responses import ResponseTitle

METHOD = "password"
LOGIN_ATTEMPT_LIMIT = 10
RESET_PURPOSE = "password_reset"


@dataclass(frozen=True, slots=True)
class ResetTicket:
    """The answer to "send me a reset code", identical for unknown addresses."""

    detail: str
    ticket: str
    expires_in: int


class PasswordService:
    """Sign up, sign in, sign out, reset and change -- with a password."""

    def enforce_policy(self, password: str, user: Any | None = None) -> None:
        """Apply the project's ``AUTH_PASSWORD_VALIDATORS`` to a proposed password."""
        try:
            validate_password(password, user)
        except ValidationError as error:
            raise AuthError(
                " ".join(error.messages), status=400, title=ResponseTitle.WEAK_PASSWORD
            ) from error

    def burn_timing(self) -> None:
        """Hash a throwaway password so a missing account costs the same as a wrong one.

        Without this, the response time alone tells an attacker which identifiers
        are real, which is most of the work of enumerating a user base.
        """
        get_user_model()().set_password("no-such-account")

    def password_matches(self, user: Any | None, password: str) -> bool:
        if user is None:
            self.burn_timing()
            return False
        return bool(user.check_password(password))

    def signup(
        self, request: HttpRequest, *, identifier: str, password: str, email: str = ""
    ) -> LoginResult:
        user_model = get_user_model()
        username_field = user_model.USERNAME_FIELD
        identifier = identifier.strip()
        if not identifier:
            raise AuthError("Enter a username.", status=400, title=ResponseTitle.USERNAME_REQUIRED)
        if username_field == "email":
            identifier = normalize_email(identifier)
        attributes = {username_field: identifier}
        if email and username_field != "email":
            attributes["email"] = normalize_email(email)
        self.enforce_policy(password)
        try:
            with transaction.atomic():
                user = user_model._default_manager.create_user(**attributes, password=password)
        except IntegrityError as error:
            raise AuthError(
                "That account already exists.", status=409, title=ResponseTitle.ACCOUNT_EXISTS
            ) from error
        record_event(request, AuthEventType.SIGNUP, user=user, method=METHOD, identifier=identifier)
        return complete_login(request, user, method=METHOD, identifier=identifier)

    def login(self, request: HttpRequest, *, identifier: str, password: str) -> LoginResult:
        identifier = identifier.strip()
        guard_attempts(f"{METHOD}:login", identifier.lower(), limit=LOGIN_ATTEMPT_LIMIT)
        try:
            user = user_by_login(identifier)
        except IdentityError:
            user = None
        if not self.password_matches(user, password):
            record_event(
                request,
                AuthEventType.LOGIN_FAILED,
                user=user,
                method=METHOD,
                identifier=identifier,
            )
            raise AuthError(
                "Those credentials are not valid.",
                status=401,
                title=ResponseTitle.INVALID_CREDENTIALS,
            )
        return complete_login(request, user, method=METHOD, identifier=identifier)

    def logout(self, request: HttpRequest, *, token: str = "") -> str:
        """Retire the presented credential. Absent or stale tokens still read as success."""
        if revoke_credentials(request, token):
            record_event(request, AuthEventType.LOGOUT, method=METHOD)
        return "Signed out."

    def forgot(self, request: HttpRequest, *, email: str) -> ResetTicket:
        """Send a reset code, answering identically for addresses with no account."""
        address = normalize_email(email)
        try:
            user = user_by_email(address)
        except IdentityError:
            user = None
        if user is None:
            ticket = decoy_challenge(RESET_PURPOSE, channel="email", destination=address)
        else:
            ticket = send_code_challenge(
                request,
                purpose=RESET_PURPOSE,
                subject=str(user.pk),
                channel="email",
                destination=address,
                method=METHOD,
                intro="Your password reset code",
            )
            record_event(
                request,
                AuthEventType.PASSWORD_RESET_REQUESTED,
                user=user,
                method=METHOD,
                identifier=address,
            )
        return ResetTicket(
            detail="If that address has an account, a reset code is on its way.",
            ticket=ticket,
            expires_in=settings.AUTH_CHALLENGE_TTL_SECONDS,
        )

    def reset(self, request: HttpRequest, *, ticket: str, code: str, password: str) -> str:
        challenge = get_challenge_store().verify(ticket, code, purpose=RESET_PURPOSE)
        user = user_from_challenge(challenge)
        self._replace_password(request, user, password, via="reset")
        return "Password updated. Sign in with your new password."

    def change(
        self, request: HttpRequest, user: Any, *, current_password: str, new_password: str
    ) -> str:
        """Change a password and retire every credential issued under the old one.

        A wrong ``current_password`` is a 400, not a 401. The caller *is*
        authenticated -- that is how they reached this at all -- and 401 means
        "authenticate and retry", which would send a client that refreshes on 401
        round a loop renewing a perfectly good token over a typo.
        """
        if not self.password_matches(user, current_password):
            raise AuthError(
                "That is not the current password for this account.",
                status=400,
                title=ResponseTitle.INCORRECT_PASSWORD,
            )
        self._replace_password(request, user, new_password, via="change")
        return "Password updated. Sign in again."

    def _replace_password(
        self, request: HttpRequest, user: Any, password: str, *, via: str
    ) -> None:
        self.enforce_policy(password, user)
        user.set_password(password)
        user.save(update_fields=["password"])
        revoked = revoke_all_for_user(user)
        record_event(
            request,
            AuthEventType.PASSWORD_CHANGED,
            user=user,
            method=METHOD,
            credentials_revoked=revoked,
            via=via,
        )


password_service = PasswordService()


def enforce_password_policy(password: str, user: Any | None = None) -> None:
    """Back-compatible alias for :meth:`PasswordService.enforce_policy`."""
    password_service.enforce_policy(password, user)


def burn_timing() -> None:
    """Back-compatible alias for :meth:`PasswordService.burn_timing`."""
    password_service.burn_timing()


def password_matches(user: Any | None, password: str) -> bool:
    """Back-compatible alias for :meth:`PasswordService.password_matches`."""
    return password_service.password_matches(user, password)
