"""One error type for "the client did something the API must refuse".

Both halves of the authentication layer need to fail with a status and a
sentence, and neither should have to reach for the other's exception class to do
it. Raising from anywhere and rendering in one place keeps endpoints readable:
a view states what went wrong and leaves the response shape alone.
"""

from django.http import HttpRequest, HttpResponse
from ninja import NinjaAPI


class ApiError(RuntimeError):
    """A failure the client caused, carrying the status it should receive."""

    def __init__(self, message: str, status: int = 400) -> None:
        super().__init__(message)
        self.status = status


def register_error_handlers(api: NinjaAPI) -> None:
    """Teach one API instance to render :class:`ApiError` and its subclasses."""

    @api.exception_handler(ApiError)
    def _api_error(request: HttpRequest, error: ApiError) -> HttpResponse:
        return api.create_response(request, {"detail": str(error)}, status=error.status)
