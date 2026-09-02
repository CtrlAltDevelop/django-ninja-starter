"""Trade the browser session a social login leaves behind for a bearer credential.

A social callback finishes in the browser: it redirects, and what the user is
left holding is a Django session cookie. Every first-party login method, by
contrast, hands back a signed token. Under any token mode but ``none`` those two
do not meet -- :func:`~infrastructure.auth.core.sessions.resolve_request_user`
reads the ``Authorization`` header and nothing else -- so a perfectly good social
sign-in would leave an API client with a cookie the API refuses.

This is the seam between them. The single-page app that the callback redirected
to calls it once, with the cookie it just received, and gets the same credential
pair a password login would have issued -- into the same tables, with the same
revocation story.

The session is consumed in the exchange. Leaving it live would mean one sign-in
carrying two independent credentials, only one of which logout can reach.
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
