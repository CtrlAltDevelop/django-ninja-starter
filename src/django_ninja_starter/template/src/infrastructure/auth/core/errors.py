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
from infrastructure.common.errors import ApiError


class AuthError(ApiError):
    """A sign-in failure the client caused. Rendered by the shared handler."""


def register_auth_exception_handlers(api: NinjaAPI) -> None:
    """Teach one API instance how to render authentication-specific failures.

    :class:`AuthError` itself is not listed: it is an :class:`ApiError`, which
    :func:`~infrastructure.common.errors.register_error_handlers` already covers.
    What is here are the domain errors that carry their own status, and the rate
    limit, which also has a header to set.
    """

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
