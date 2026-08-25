"""Sign up and sign in by following a single-use link.

The link's token is the whole credential, so unlike the code flows nothing is
returned to the caller who asked for it -- the token exists only in the message.
The landing page reads it out of the URL and posts it back to ``/verify``.

Both flows mint the same kind of token and record which one asked for it in the
challenge metadata. Redeeming is therefore a single lookup: probing one purpose
and then the other would not work, because presenting a ticket under the wrong
purpose deliberately destroys it.
"""

from urllib.parse import urlencode

from django.conf import settings
from django.http import HttpRequest
from ninja import Router

from infrastructure.auth.core.challenges import get_challenge_store
from infrastructure.auth.core.delivery import send_email
from infrastructure.auth.core.errors import AuthError
from infrastructure.auth.core.flows import complete_login, consume_codeless, record_event
from infrastructure.auth.core.identities import (
    create_user_for_email,
    normalize_email,
    user_by_email,
)
from infrastructure.auth.core.models import AuthEventType
from infrastructure.auth.core.schemas import LoginOut, MessageOut, login_out, mask
from infrastructure.auth.core.sessions import revoke_credentials
from infrastructure.auth.core.throttling import guard_delivery
from infrastructure.auth.magic_link.schemas import LogoutIn, StartIn, StartOut, VerifyIn

router = Router()
METHOD = "magic_link"
PURPOSE = "magic_link"
SIGNUP = "signup"
LOGIN = "login"


def _link(token: str) -> str:
    base = settings.AUTH_MAGIC_LINK_BASE_URL
    separator = "&" if "?" in base else "?"
    return f"{base}{separator}{urlencode({'token': token})}"


def _start(request: HttpRequest, raw_email: str, intent: str, intro: str) -> StartOut:
    email = normalize_email(raw_email)
    guard_delivery(f"{METHOD}:email", email)
    token = get_challenge_store().create(
        purpose=PURPOSE,
        subject="",
        channel="email",
        destination=email,
        metadata={"intent": intent},
    )
    send_email(email, intro, f"{intro}: {_link(token)}\n\nThis link can be used once.")
    record_event(
        request,
        AuthEventType.CODE_SENT,
        method=METHOD,
        identifier=email,
        channel="email",
        intent=intent,
    )
    return StartOut(
        detail="Check your inbox for the link.",
        destination=mask("email", email),
        expires_in=settings.AUTH_CHALLENGE_TTL_SECONDS,
    )


@router.post(
    "/signup/start",
    response={200: StartOut, 400: MessageOut, 429: MessageOut},
    auth=None,
    summary="Email a sign-up link",
)
def signup_start(request: HttpRequest, payload: StartIn) -> StartOut:
    return _start(request, payload.email, SIGNUP, "Your sign-up link")


@router.post(
    "/login/start",
    response={200: StartOut, 400: MessageOut, 429: MessageOut},
    auth=None,
    summary="Email a sign-in link",
)
def login_start(request: HttpRequest, payload: StartIn) -> StartOut:
    return _start(request, payload.email, LOGIN, "Your sign-in link")


@router.post(
    "/verify",
    response={200: LoginOut, 400: MessageOut, 403: MessageOut, 404: MessageOut, 410: MessageOut},
    auth=None,
    summary="Sign in with a link token",
)
def verify(request: HttpRequest, payload: VerifyIn) -> LoginOut:
    """Redeem a link token, whichever of the two flows minted it."""
    challenge = consume_codeless(payload.token, PURPOSE)
    email = challenge.destination
    intent = str(challenge.metadata.get("intent", LOGIN))
    user = user_by_email(email)
    if user is None:
        if intent == LOGIN and not settings.AUTH_AUTO_CREATE_USERS:
            raise AuthError("No account uses that address.", status=404)
        user = create_user_for_email(email)
        record_event(
            request,
            AuthEventType.SIGNUP,
            user=user,
            method=METHOD,
            identifier=email,
        )
    return login_out(complete_login(request, user, method=METHOD, identifier=email))


@router.post("/logout", response=MessageOut, auth=None, summary="Sign out")
def logout(request: HttpRequest, payload: LogoutIn) -> MessageOut:
    if revoke_credentials(request, payload.token):
        record_event(request, AuthEventType.LOGOUT, method=METHOD)
    return MessageOut(detail="Signed out.")
