"""One place to decide what each authentication failure looks like on the wire.

Handlers are attached to the API rather than wrapped around every endpoint, so a
view can raise the domain error that actually happened and stay readable.
"""

from django.http import HttpRequest, HttpResponse
from ninja import NinjaAPI

from infrastructure.auth.core.challenges import (
    ChallengeAttemptsExhausted,
    ChallengeError,
    ChallengeExpired,
    InvalidCode,
)
from infrastructure.auth.core.identities import IdentityError
from infrastructure.auth.core.throttling import RateLimited


class AuthError(RuntimeError):
    """A failure the client caused, carrying the status it should receive."""

    def __init__(self, message: str, status: int = 400) -> None:
        super().__init__(message)
        self.status = status


def register_auth_exception_handlers(api: NinjaAPI) -> None:
    """Teach one API instance how to render authentication failures."""

    @api.exception_handler(AuthError)
    def _auth_error(request: HttpRequest, error: AuthError) -> HttpResponse:
        return api.create_response(request, {"detail": str(error)}, status=error.status)

    @api.exception_handler(RateLimited)
    def _rate_limited(request: HttpRequest, error: RateLimited) -> HttpResponse:
        response = api.create_response(request, {"detail": str(error)}, status=429)
        response["Retry-After"] = str(error.retry_after)
        return response

    @api.exception_handler(ChallengeAttemptsExhausted)
    def _exhausted(request: HttpRequest, error: ChallengeAttemptsExhausted) -> HttpResponse:
        return api.create_response(request, {"detail": str(error)}, status=429)

    @api.exception_handler(ChallengeExpired)
    def _expired(request: HttpRequest, error: ChallengeExpired) -> HttpResponse:
        return api.create_response(request, {"detail": str(error)}, status=410)

    @api.exception_handler(InvalidCode)
    def _invalid_code(request: HttpRequest, error: InvalidCode) -> HttpResponse:
        return api.create_response(request, {"detail": str(error)}, status=400)

    @api.exception_handler(ChallengeError)
    def _challenge_error(request: HttpRequest, error: ChallengeError) -> HttpResponse:
        return api.create_response(request, {"detail": str(error)}, status=400)

    @api.exception_handler(IdentityError)
    def _identity_error(request: HttpRequest, error: IdentityError) -> HttpResponse:
        return api.create_response(request, {"detail": str(error)}, status=400)
