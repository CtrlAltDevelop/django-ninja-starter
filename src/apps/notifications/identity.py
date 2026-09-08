"""Working out who is on the other end of a socket.

A WebSocket handshake is an HTTP request, so a browser will send cookies on it
and a native client can set headers -- but neither is available to the two
places people actually put a token: ``?token=`` on the URL, because the browser
``WebSocket`` constructor cannot set headers at all, and the subprotocol field,
because it is the one header the constructor *can* set. All four are accepted
here, and the socket also takes a token in a message after connecting, which is
the flow this app is built around: join, hear the public traffic, then say who
you are and start hearing your own.

Nothing here decides what a token means. That is
``infrastructure.auth.core.sessions``, which already knows which credential the
project's configured token mode issues; this module only shapes what a socket
has into what that function reads, and copes with the auth app not being
installed at all -- in which case there are no tokens, and a session cookie from
Django's own login is the only identity a socket can have.
"""

from dataclasses import dataclass
from http.cookies import SimpleCookie
from importlib import import_module
from types import SimpleNamespace
from typing import Any, cast
from urllib.parse import parse_qs

from django.conf import settings
from django.contrib.auth import get_user
from django.http import HttpRequest

BEARER_SUBPROTOCOL = "bearer"


@dataclass(frozen=True)
class Credentials:
    """Whatever the handshake carried. Any of these may be empty."""

    token: str = ""
    session_key: str = ""
    subprotocol: str = ""
    """The subprotocol to echo on accept. A browser that offered one and is
    answered with none closes the connection itself, so this is not cosmetic."""


def _header(scope: dict[str, Any], name: str) -> str:
    """Read one handshake header. ASGI gives them as lowercased byte pairs."""
    wanted = name.encode()
    for key, value in scope.get("headers") or []:
        if key == wanted:
            return value.decode("latin-1")
    return ""


def _token_from_subprotocol(offered: str) -> tuple[str, str]:
    """Read ``Sec-WebSocket-Protocol: bearer, <token>``, the browser's only header.

    Returns the token and the protocol to answer with. Anything that does not
    start with ``bearer`` is left alone: it belongs to some other negotiation,
    and picking a protocol we do not implement would be worse than picking none.
    """
    parts = [part.strip() for part in offered.split(",") if part.strip()]
    if len(parts) >= 2 and parts[0].lower() == BEARER_SUBPROTOCOL:
        return parts[1], parts[0]
    return "", ""


def credentials_from_scope(scope: dict[str, Any]) -> Credentials:
    """Pull a token or a session key out of the handshake, in order of explicitness."""
    query = parse_qs(cast(bytes, scope.get("query_string", b"")).decode("utf-8"))
    token = (query.get("token") or [""])[0].strip()

    subprotocol = ""
    if not token:
        token, subprotocol = _token_from_subprotocol(_header(scope, "sec-websocket-protocol"))
    if not token:
        scheme, _, value = _header(scope, "authorization").partition(" ")
        if scheme.lower() == BEARER_SUBPROTOCOL:
            token = value.strip()

    cookies = SimpleCookie()
    cookies.load(_header(scope, "cookie"))
    session_cookie = cookies.get(settings.SESSION_COOKIE_NAME)

    return Credentials(
        token=token,
        session_key=session_cookie.value if session_cookie else "",
        subprotocol=subprotocol,
    )


def user_from_token(token: str) -> Any | None:
    """Resolve the account a bearer token belongs to, or ``None``.

    The project's own resolver is reused rather than re-implemented, so a socket
    accepts exactly the credentials the API accepts -- including refusing one
    minted under a token mode the project no longer runs. It reads the token off
    a request, so it is given the smallest thing that looks like one.
    """
    if not token:
        return None
    try:
        from infrastructure.auth.core.sessions import resolve_request_user
    except ImportError:  # pragma: no cover - only in a project without the auth apps
        return None
    request = SimpleNamespace(META={"HTTP_AUTHORIZATION": f"Bearer {token}"})
    return resolve_request_user(cast(HttpRequest, request))


def user_from_session(session_key: str) -> Any | None:
    """Resolve the account behind a session cookie, or ``None``.

    Django's own ``get_user`` does the work, which means the session auth hash is
    verified: a cookie that outlived a password change identifies nobody.
    """
    if not session_key:
        return None
    engine = import_module(settings.SESSION_ENGINE)
    request = SimpleNamespace(session=engine.SessionStore(session_key))
    user = get_user(cast(HttpRequest, request))
    return user if user.is_authenticated and user.is_active else None


def user_from_credentials(credentials: Credentials) -> Any | None:
    """A token if there is one, the session cookie if there is not."""
    return user_from_token(credentials.token) or user_from_session(credentials.session_key)


def user_summary(user: Any) -> dict[str, Any]:
    """Enough for a client to confirm it authenticated as who it meant to."""
    return {
        "id": str(user.pk),
        "username": user.get_username(),
        "email": getattr(user, "email", ""),
    }
