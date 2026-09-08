"""One place to decide what each authentication failure looks like on the wire.

Handlers are attached to the API rather than wrapped around every endpoint, so a
view can raise the domain error that actually happened and stay readable.

What each handler adds beyond the status is the
:class:`~infrastructure.common.responses.ResponseTitle`: an expired code and a
wrong code are both things a user needs told, in their own language, and neither
is distinguishable from the status alone.
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
from infrastructure.common.responses import ResponseTitle, envelope


class AuthError(ApiError):
    """A sign-in failure the client caused. Rendered by the shared handler."""


def register_auth_exception_handlers(api: NinjaAPI) -> None:
    """Teach one API instance how to render authentication-specific failures.

    :class:`AuthError` itself is not listed: it is an :class:`ApiError`, which
    :func:`~infrastructure.common.errors.register_error_handlers` already covers.
    What is here are the domain errors that carry their own status, and the rate
    limit, which also has a header to set.
    """

    def _respond(
        request: HttpRequest,
        error: Exception,
        *,
        status: int,
        title: ResponseTitle,
    ) -> HttpResponse:
        return api.create_response(
            request,
            envelope(
                status=status,
                errors=[str(error)],
                title=title,
                description=str(error),
            ),
            status=status,
        )

    @api.exception_handler(RateLimited)
    def _rate_limited(request: HttpRequest, error: RateLimited) -> HttpResponse:
        response = _respond(request, error, status=429, title=ResponseTitle.RATE_LIMITED)
        response["Retry-After"] = str(error.retry_after)
        return response

    @api.exception_handler(ChallengeAttemptsExhausted)
    def _exhausted(request: HttpRequest, error: ChallengeAttemptsExhausted) -> HttpResponse:
        return _respond(request, error, status=429, title=ResponseTitle.TOO_MANY_ATTEMPTS)

    @api.exception_handler(ChallengeExpired)
    def _expired(request: HttpRequest, error: ChallengeExpired) -> HttpResponse:
        return _respond(request, error, status=410, title=ResponseTitle.CODE_EXPIRED)

    @api.exception_handler(InvalidCode)
    def _invalid_code(request: HttpRequest, error: InvalidCode) -> HttpResponse:
        return _respond(request, error, status=400, title=ResponseTitle.INVALID_CODE)

    @api.exception_handler(ChallengeError)
    def _challenge_error(request: HttpRequest, error: ChallengeError) -> HttpResponse:
        return _respond(request, error, status=400, title=ResponseTitle.CHALLENGE_INVALID)

    @api.exception_handler(IdentityError)
    def _identity_error(request: HttpRequest, error: IdentityError) -> HttpResponse:
        return _respond(request, error, status=400, title=ResponseTitle.INVALID_IDENTIFIER)
