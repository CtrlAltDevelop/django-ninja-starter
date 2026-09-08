"""One error type for "the client did something the API must refuse".

Both halves of the authentication layer need to fail with a status and a
sentence, and neither should have to reach for the other's exception class to do
it. Raising from anywhere and rendering in one place keeps endpoints readable:
a view states what went wrong and leaves the response shape alone.

What an error carries beyond its status is a
:class:`~infrastructure.common.responses.ResponseTitle` -- the key a client
translates. Passing one is how a refusal says something more useful than its
status code: two different 400s can be told apart by a client that has never
read the English sentence next to them. Leaving it off is fine where the status
already says everything, and the title is then derived from it.
"""

from collections.abc import Iterable
from typing import Any

from django.http import Http404, HttpRequest, HttpResponse
from ninja import NinjaAPI
from ninja.errors import HttpError, ValidationError

from infrastructure.common.responses import ResponseTitle, envelope, title_for_status


class ApiError(RuntimeError):
    """A failure the client caused, carrying the status it should receive."""

    def __init__(
        self,
        message: str,
        status: int = 400,
        *,
        title: ResponseTitle | None = None,
        errors: Iterable[str] | None = None,
    ) -> None:
        super().__init__(message)
        self.status = status
        self.title = title or title_for_status(status)
        self.errors = list(errors) if errors is not None else [message]


def _validation_messages(errors: list[dict[str, Any]]) -> list[str]:
    """Flatten pydantic's error dictionaries into one sentence each.

    ``body.payload.email: Field required`` tells a developer which field and
    why; the nested structure underneath it does not survive translation and is
    not worth putting on the wire.
    """
    messages = []
    for error in errors:
        location = ".".join(str(part) for part in error.get("loc", ()))
        message = str(error.get("msg", "Invalid value"))
        messages.append(f"{location}: {message}" if location else message)
    return messages


def register_error_handlers(api: NinjaAPI) -> None:
    """Teach one API instance to render every refusal in the same envelope.

    The last three replace handlers Django Ninja installs itself. Theirs answer
    with a bare ``{"detail": ...}``, which would be the only shape a client had
    to special-case -- and it would show up on the paths hardest to test for, as
    those errors are raised by the framework rather than by any view here.
    """

    @api.exception_handler(ApiError)
    def _api_error(request: HttpRequest, error: ApiError) -> HttpResponse:
        return api.create_response(
            request,
            envelope(
                status=error.status,
                errors=error.errors,
                title=error.title,
                description=str(error),
            ),
            status=error.status,
        )

    @api.exception_handler(ValidationError)
    def _validation_error(request: HttpRequest, error: ValidationError) -> HttpResponse:
        messages = _validation_messages(error.errors)
        return api.create_response(
            request,
            envelope(
                status=422,
                errors=messages,
                title=ResponseTitle.VALIDATION_ERROR,
                description="; ".join(messages),
            ),
            status=422,
        )

    @api.exception_handler(Http404)
    def _not_found(request: HttpRequest, error: Http404) -> HttpResponse:
        return api.create_response(
            request,
            envelope(status=404, title=ResponseTitle.NOT_FOUND),
            status=404,
        )

    @api.exception_handler(HttpError)
    def _http_error(request: HttpRequest, error: HttpError) -> HttpResponse:
        """Covers what the framework raises on its own: no credential, throttled."""
        status = error.status_code
        derived = title_for_status(status)
        title = ResponseTitle.AUTHENTICATION_REQUIRED if status == 401 else derived
        return api.create_response(
            request,
            envelope(status=status, errors=[str(error)], title=title, description=str(error)),
            status=status,
        )
