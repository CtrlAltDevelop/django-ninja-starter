"""Render this project's refusals as gRPC statuses.

The mapping is the obvious one, and deliberately narrow: gRPC's status space is
smaller than HTTP's, so several statuses land on the same code and the detail
carries the `title` a client keys its translations off -- the same vocabulary
the REST envelope and the GraphQL `extensions` use.
"""

from collections.abc import Awaitable, Callable
from functools import wraps
from typing import Any

import grpc

from infrastructure.common.errors import ApiError
from infrastructure.common.responses import ResponseTitle

STATUS_CODES = {
    400: grpc.StatusCode.INVALID_ARGUMENT,
    401: grpc.StatusCode.UNAUTHENTICATED,
    403: grpc.StatusCode.PERMISSION_DENIED,
    404: grpc.StatusCode.NOT_FOUND,
    409: grpc.StatusCode.ALREADY_EXISTS,
    410: grpc.StatusCode.NOT_FOUND,
    429: grpc.StatusCode.RESOURCE_EXHAUSTED,
    503: grpc.StatusCode.UNAVAILABLE,
}


async def abort(context: Any, error: ApiError) -> None:
    """End the call with the status and the title this refusal carries."""
    code = STATUS_CODES.get(error.status, grpc.StatusCode.UNKNOWN)
    await context.abort(code, f"{error.title}: {error}")


def require_caller(user: Any | None) -> Any:
    """Return the caller, or refuse the way an unauthenticated REST call is refused."""
    if user is None:
        raise ApiError(
            "Authentication required.",
            status=401,
            title=ResponseTitle.AUTHENTICATION_REQUIRED,
        )
    return user


def action(function: Callable[..., Awaitable[Any]]) -> Callable[..., Awaitable[Any]]:
    """Let a gRPC action refuse by raising, the way a REST endpoint does.

    Applied *below* ``@grpc_action``: that decorator turns the function into a
    registered action, so it has to see the wrapped one.
    """

    @wraps(function)
    async def wrapper(self: Any, request: Any, context: Any) -> Any:
        try:
            return await function(self, request, context)
        except ApiError as error:
            await abort(context, error)

    return wrapper
