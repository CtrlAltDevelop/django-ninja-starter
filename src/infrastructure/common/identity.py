"""Who is calling, whichever transport they called over.

REST has Django Ninja's ``api_auth``; GraphQL has a Django request it can hand
to the same resolver; gRPC has invocation metadata and no request at all. All
three end at the one function that knows how to read this project's credential,
so a token that works on one door works on all of them, and revoking it closes
all of them at once.

The import of the authentication core is deliberately lazy. A project can be
generated with no login method at all, and this module has to keep working
there -- it just never finds anybody.
"""

from typing import Any

from asgiref.sync import sync_to_async
from django.conf import settings
from django.http import HttpRequest, HttpResponse
from ninja.utils import check_csrf

# gRPC metadata keys are lowercase; Django keeps headers in `META` under their
# CGI names. This is the one header the credential is ever carried in.
AUTHORIZATION_METADATA_KEY = "authorization"
AUTHORIZATION_META_KEY = "HTTP_AUTHORIZATION"


def _resolver() -> Any:
    """Return the credential resolver, or ``None`` where nothing signs people in."""
    if not settings.AUTH_INSTALLED_APPS and settings.AUTH_TOKEN_MODE == "none":
        return None
    from infrastructure.auth.core.sessions import resolve_request_user

    return resolve_request_user


def session_csrf_failure(request: HttpRequest) -> HttpResponse | None:
    """Return Django's CSRF refusal if this request rides on the session cookie.

    A bearer token is not an ambient credential -- a browser never attaches one
    on its own -- so a request it authenticates cannot be forged cross-site and
    needs no CSRF token. The session cookie is attached to every request the
    browser makes, so a write it authenticates must prove it came from our own
    pages. The session is only ever the credential in the ``none`` token mode;
    every other mode ignores it, so there is nothing to forge there. Both REST
    and GraphQL are CSRF-exempt at the Django level and ask this instead, which
    is what keeps the two transports agreeing. Safe methods pass untouched.
    """
    if settings.AUTH_TOKEN_MODE != "none":
        return None
    user = getattr(request, "user", None)
    if user is None or not user.is_authenticated:
        return None
    return check_csrf(request)


def caller(request: HttpRequest) -> Any | None:
    """Return the user a request proves it is, or ``None``.

    Used by the GraphQL view, whose request is an ordinary Django one carrying
    an ordinary ``Authorization`` header.
    """
    resolve = _resolver()
    if resolve is None:
        user = getattr(request, "user", None)
        return user if user is not None and user.is_authenticated else None
    return resolve(request)


def _grpc_caller(context: Any) -> Any | None:
    request = getattr(context, "http_request", None)
    if request is None:
        return None
    if AUTHORIZATION_META_KEY not in request.META:
        header = request.META.get(AUTHORIZATION_METADATA_KEY)
        if header:
            request.META[AUTHORIZATION_META_KEY] = header
    return caller(request)


async def grpc_caller(context: Any) -> Any | None:
    """Return the user a gRPC call proves it is, or ``None``.

    django-socio-grpc's stand-in request copies invocation metadata into ``META``
    under the metadata's own lowercase keys, so the credential arrives as
    ``authorization`` where the resolver looks for ``HTTP_AUTHORIZATION``. Moving
    it across is all that separates a gRPC caller from an HTTP one.

    Awaitable because the gRPC server is: checking a credential reads a row, and
    the ORM refuses to be called from the event loop.
    """
    return await sync_to_async(_grpc_caller)(context)
