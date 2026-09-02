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
"""

from django.conf import settings
from django.contrib.auth import logout as django_logout
from django.http import HttpRequest
from ninja import Router

from infrastructure.common.errors import ApiError
from infrastructure.common.responses import ResponseTitle
from infrastructure.oauth.core.schemas import CredentialsOut, MessageOut, credentials_out

router = Router()

METHOD = "social"


@router.post(
    "/exchange",
    response={200: CredentialsOut, 401: MessageOut, 409: MessageOut},
    auth=None,
    summary="Exchange a browser session for a bearer credential",
)
def exchange(request: HttpRequest) -> CredentialsOut:
    """Turn the session a social callback established into a token pair."""
    from infrastructure.auth.core.flows import record_event
    from infrastructure.auth.core.models import AuthEventType
    from infrastructure.auth.core.sessions import issue_credentials

    if settings.AUTH_TOKEN_MODE == "none":
        # There is nothing to exchange for: under this mode the session cookie
        # *is* the credential the API accepts.
        raise ApiError(
            "This deployment issues no bearer tokens.",
            status=409,
            title=ResponseTitle.TOKENS_UNSUPPORTED,
        )

    user = getattr(request, "user", None)
    if user is None or not user.is_authenticated:
        raise ApiError(
            "No signed-in session to exchange.", status=401, title=ResponseTitle.NOT_SIGNED_IN
        )
    if not user.is_active:
        raise ApiError(
            "This account is disabled.", status=401, title=ResponseTitle.ACCOUNT_DISABLED
        )

    method = str(request.session.get("social_auth_method", "") or METHOD)
    credentials = issue_credentials(request, user, method=method)
    # `issue_credentials` does not touch the session for a token mode, so the
    # cookie has to be retired here or it outlives the exchange.
    django_logout(request)
    record_event(request, AuthEventType.LOGIN_SUCCEEDED, user=user, method=method)
    return credentials_out(credentials)


# What the audit trail calls a credential minted from an admin session. It is a
# real login -- it produces a real token -- so it is recorded as one, under a
# name that says where it came from rather than being filed as a password login.
ADMIN_SESSION_METHOD = "admin_session"


def token_from_session(request: HttpRequest) -> CredentialsOut:
    """Mint a bearer credential for the staff member whose session is presenting.

    Deliberately *not* a session-consuming exchange like :func:`exchange` above:
    this is called by the Swagger page on load, and logging somebody out of the
    admin for opening the documentation would be a poor trade.

    Staff-only, because "signed into the admin" is the case this exists for, and
    an end user who happens to hold a session cookie is not it.
    """
    from infrastructure.auth.core.flows import record_event
    from infrastructure.auth.core.models import AuthEventType
    from infrastructure.auth.core.sessions import issue_credentials

    if settings.AUTH_TOKEN_MODE == "none":
        # Nothing to mint: under this mode the session cookie *is* the credential
        # the API accepts, so the docs page already works as it stands.
        raise ApiError(
            "This deployment issues no bearer tokens.",
            status=409,
            title=ResponseTitle.TOKENS_UNSUPPORTED,
        )

    user = getattr(request, "user", None)
    if user is None or not user.is_authenticated:
        raise ApiError(
            "No signed-in session to mint a token for.",
            status=401,
            title=ResponseTitle.NOT_SIGNED_IN,
        )
    # Django's own backend already refuses to resolve a session belonging to a
    # deactivated account, so this is belt-and-braces for a project that has
    # swapped AUTHENTICATION_BACKENDS for one less careful.
    if not user.is_active:
        raise ApiError(
            "This account is disabled.", status=401, title=ResponseTitle.ACCOUNT_DISABLED
        )
    if not user.is_staff:
        raise ApiError(
            "Only staff may mint a token from a session.",
            status=403,
            title=ResponseTitle.FORBIDDEN,
        )

    credentials = issue_credentials(request, user, method=ADMIN_SESSION_METHOD)
    record_event(request, AuthEventType.LOGIN_SUCCEEDED, user=user, method=ADMIN_SESSION_METHOD)
    return credentials_out(credentials)


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
