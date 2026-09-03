"""Render this project's refusals as GraphQL errors.

REST puts the refusal in an envelope with a `title` a client keys its
translations off. GraphQL has no envelope, but it does have `extensions`, which
is the same idea in the shape the protocol already has: the sentence is for a
developer, the `title` is for the client.
"""

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from functools import wraps
from typing import Any

from asgiref.sync import sync_to_async
from graphql import GraphQLError

from infrastructure.common.errors import ApiError
from infrastructure.common.responses import ResponseTitle


@contextmanager
def as_graphql_errors() -> Iterator[None]:
    """Translate an :class:`ApiError` raised inside into a GraphQL error."""
    try:
        yield
    except ApiError as error:
        raise GraphQLError(
            str(error),
            extensions={
                "title": str(error.title),
                "status": error.status,
                "errors": error.errors,
            },
        ) from error


def resolver[F: Callable[..., Any]](function: F) -> F:
    """Wrap a synchronous resolver for the asynchronous GraphQL view.

    The endpoint is one async view because one app -- the CMS -- reads
    asynchronously, and a schema cannot be half of each. Every other service in
    this project is ordinary synchronous Django, so its resolvers cross over
    here, once, rather than each of them remembering to.
    """

    @wraps(function)
    async def wrapper(*args: Any, **kwargs: Any) -> Any:
        def call() -> Any:
            with as_graphql_errors():
                return function(*args, **kwargs)

        return await sync_to_async(call)()

    return wrapper  # type: ignore[return-value]


def async_resolver[F: Callable[..., Any]](function: F) -> F:
    """The same, for a resolver whose service is already asynchronous."""

    @wraps(function)
    async def wrapper(*args: Any, **kwargs: Any) -> Any:
        with as_graphql_errors():
            return await function(*args, **kwargs)

    return wrapper  # type: ignore[return-value]


def require_caller(user: Any | None) -> Any:
    """Return the caller, or refuse the way an unauthenticated REST call is refused."""
    if user is None:
        raise ApiError(
            "Authentication required.",
            status=401,
            title=ResponseTitle.AUTHENTICATION_REQUIRED,
        )
    return user
