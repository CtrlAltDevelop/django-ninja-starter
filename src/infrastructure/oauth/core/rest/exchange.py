"""The seam between a Django session and a bearer credential.

Under any token mode but ``none``,
:func:`~infrastructure.auth.core.sessions.resolve_request_user` reads the
``Authorization`` header and nothing else, so a session cookie -- however
legitimately it was obtained -- is a credential the API refuses. Two different
people arrive holding one, and each gets an endpoint here.

**A social login** finishes in the browser: it redirects, and what the user is
left with is a cookie. ``/exchange`` turns it into the same credential pair a
password login would have issued, into the same tables, with the same revocation
story. That one **consumes the session**, because leaving it live would mean one
sign-in carrying two independent credentials, only one of which logout can reach.

**A staff member reading the API documentation** is signed into the admin and
wants "Try it out" to work. ``/from-session`` mints them a token without
disturbing the session, since taking the admin login away as the price of
opening the docs page would be an absurd trade. It is staff-only and it is
published only when ``AUTH_SESSION_TOKEN_FOR_STAFF`` allows it.

Both decisions are :class:`SessionExchangeService`'s; this file is the door.
"""

from django.conf import settings
from django.http import HttpRequest
from ninja import Router

from infrastructure.oauth.core.rest.schemas import CredentialsOut, MessageOut, credentials_out
from infrastructure.oauth.core.services import session_exchange_service

router = Router()


@router.post(
    "/exchange",
    response={200: CredentialsOut, 401: MessageOut, 409: MessageOut},
    auth=None,
    summary="Exchange a browser session for a bearer credential",
)
def exchange(request: HttpRequest) -> CredentialsOut:
    """Turn the session a social callback established into a token pair."""
    return credentials_out(session_exchange_service.exchange(request))


def token_from_session(request: HttpRequest) -> CredentialsOut:
    """Mint a bearer credential for the staff member whose session is presenting."""
    return credentials_out(session_exchange_service.from_session(request))


# Registered conditionally rather than refusing at call time, so that a
# deployment which has turned this off does not *document* a session-to-token
# bridge it will not perform. An absent route is a 404 and an absent line in the
# OpenAPI document; a registered one that always answers 403 is neither.
if settings.AUTH_SESSION_TOKEN_FOR_STAFF:
    router.post(
        "/from-session",
        response={200: CredentialsOut, 401: MessageOut, 403: MessageOut, 409: MessageOut},
        auth=None,
        summary="Mint a bearer credential for the signed-in admin session",
    )(token_from_session)
