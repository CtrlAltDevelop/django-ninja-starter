"""Everything magic-link login decides, independent of who asked.

The link's token is the whole credential, so unlike the code flows nothing is
returned to the caller who asked for it -- the token exists only in the message.
The landing page reads it out of the URL and posts it back to be redeemed.

Both flows mint the same kind of token and record which one asked for it in the
challenge metadata. Redeeming is therefore a single lookup: probing one purpose
and then the other would not work, because presenting a ticket under the wrong
purpose deliberately destroys it.
"""

from dataclasses import dataclass
from urllib.parse import urlencode

from django.conf import settings
from django.http import HttpRequest

from infrastructure.accounts.profiles import confirm_email
from infrastructure.auth.core.challenges import get_challenge_store
from infrastructure.auth.core.delivery import send_email
from infrastructure.auth.core.errors import AuthError
from infrastructure.auth.core.flows import (
    LoginResult,
    complete_login,
    consume_codeless,
    record_event,
)
from infrastructure.auth.core.identities import (
    create_user_for_email,
    normalize_email,
    user_by_email,
)
from infrastructure.auth.core.models import AuthEventType
from infrastructure.auth.core.services import masking_service
from infrastructure.auth.core.sessions import revoke_credentials
from infrastructure.auth.core.throttling import guard_delivery
from infrastructure.common.responses import ResponseTitle

METHOD = "magic_link"
PURPOSE = "magic_link"
SIGNUP = "signup"
LOGIN = "login"


@dataclass(frozen=True, slots=True)
class LinkSent:
    """What a caller learns after asking for a link: that one is on its way."""

    detail: str
    destination: str
    expires_in: int


class MagicLinkService:
    """Sign up and sign in by following a single-use link."""

    def start_signup(self, request: HttpRequest, *, email: str) -> LinkSent:
        return self._start(request, email, SIGNUP, "Your sign-up link")

    def start_login(self, request: HttpRequest, *, email: str) -> LinkSent:
        return self._start(request, email, LOGIN, "Your sign-in link")

    def verify(self, request: HttpRequest, *, token: str) -> LoginResult:
        """Redeem a link token, whichever of the two flows minted it."""
        challenge = consume_codeless(token, PURPOSE)
        email = challenge.destination
        intent = str(challenge.metadata.get("intent", LOGIN))
        user = user_by_email(email)
        if user is None:
            if intent == LOGIN and not settings.AUTH_AUTO_CREATE_USERS:
                raise AuthError(
                    "No account uses that address.",
                    status=404,
                    title=ResponseTitle.ACCOUNT_NOT_FOUND,
                )
            user = create_user_for_email(email)
            record_event(request, AuthEventType.SIGNUP, user=user, method=METHOD, identifier=email)
        confirm_email(user, email)
        return complete_login(request, user, method=METHOD, identifier=email)

    def logout(self, request: HttpRequest, *, token: str = "") -> str:
        if revoke_credentials(request, token):
            record_event(request, AuthEventType.LOGOUT, method=METHOD)
        return "Signed out."

    def link(self, token: str) -> str:
        base = settings.AUTH_MAGIC_LINK_BASE_URL
        separator = "&" if "?" in base else "?"
        return f"{base}{separator}{urlencode({'token': token})}"

    def _start(self, request: HttpRequest, raw_email: str, intent: str, intro: str) -> LinkSent:
        email = normalize_email(raw_email)
        guard_delivery(f"{METHOD}:email", email)
        token = get_challenge_store().create(
            purpose=PURPOSE,
            subject="",
            channel="email",
            destination=email,
            metadata={"intent": intent},
        )
        send_email(email, intro, f"{intro}: {self.link(token)}\n\nThis link can be used once.")
        record_event(
            request,
            AuthEventType.CODE_SENT,
            method=METHOD,
            identifier=email,
            channel="email",
            intent=intent,
        )
        return LinkSent(
            detail="Check your inbox for the link.",
            destination=masking_service.destination("email", email),
            expires_in=settings.AUTH_CHALLENGE_TTL_SECONDS,
        )


magic_link_service = MagicLinkService()
