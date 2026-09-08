"""What the three token modes have in common, independent of transport.

Each mode owns its own tables and its own idea of what "refresh" means, and
:class:`TokenModeService` is the shape all three answer in -- which is what lets
one client talk to a deployment without knowing which mode is behind
``/auth/token``.

The session-to-credential exchanges live here too. They are not a mode's
business: both ask ``issue_credentials`` for whatever this deployment issues.
"""

from dataclasses import dataclass
from typing import Any

from django.conf import settings
from django.contrib.auth import logout as django_logout
from django.http import HttpRequest

from infrastructure.common.errors import ApiError
from infrastructure.common.responses import ResponseTitle
from infrastructure.oauth.core.credentials import IssuedCredentials

SOCIAL_METHOD = "social"
# What the audit trail calls a credential minted from an admin session. It is a
# real login -- it produces a real token -- so it is recorded as one, under a
# name that says where it came from rather than being filed as a password login.
ADMIN_SESSION_METHOD = "admin_session"


@dataclass(frozen=True, slots=True)
class SessionView:
    """One live credential, as an account's own device list would show it."""

    session_id: str
    created_at: str
    last_used_at: str
    expires_at: str
    ip_address: str
    user_agent: str
    auth_method: str
    current: bool = False


@dataclass(frozen=True, slots=True)
class SessionList:
    """Every live credential on an account, and which mode issued them."""

    mode: str
    sessions: list[SessionView]


class TokenModeService:
    """The four things every token mode can do, in one shape.

    Subclasses fill in the parts that differ. Everything a transport calls is
    declared here, so a router, a resolver and a servicer can be written against
    the mode-independent surface rather than against whichever mode is active.
    """

    mode: str

    def refresh(self, request: HttpRequest, *, refresh_token: str = "") -> IssuedCredentials:
        raise NotImplementedError

    def sessions(self, user: Any) -> SessionList:
        raise NotImplementedError

    def end_session(self, user: Any, session_id: str) -> str:
        """Revoke one named credential, or refuse if the account has no such one."""
        if not self.revoke_session(user, session_id):
            raise ApiError("No such session.", status=404, title=ResponseTitle.SESSION_NOT_FOUND)
        return "Session ended."

    def revoke_session(self, user: Any, session_id: str) -> bool:
        raise NotImplementedError

    def revoke(self, request: HttpRequest, *, token: str = "") -> str:
        """Retire a credential. A token already gone reads as success, not failure."""
        from infrastructure.auth.core.sessions import revoke_credentials

        revoke_credentials(request, token)
        return "Credential revoked."


class SessionExchangeService:
    """Turn a browser session into the credential this deployment issues.

    Two different people arrive holding a session cookie, and they want opposite
    things done with it -- see :mod:`infrastructure.oauth.core.rest.exchange` for
    why one consumes the session and the other must not.
    """

    def exchange(self, request: HttpRequest) -> IssuedCredentials:
        """Turn the session a social callback established into a token pair."""
        from infrastructure.auth.core.flows import record_event
        from infrastructure.auth.core.models import AuthEventType
        from infrastructure.auth.core.sessions import issue_credentials

        user = self._signed_in(request, "No signed-in session to exchange.")
        method = str(request.session.get("social_auth_method", "") or SOCIAL_METHOD)
        credentials = issue_credentials(request, user, method=method)
        # `issue_credentials` does not touch the session for a token mode, so the
        # cookie has to be retired here or it outlives the exchange.
        django_logout(request)
        record_event(request, AuthEventType.LOGIN_SUCCEEDED, user=user, method=method)
        return credentials

    def from_session(self, request: HttpRequest) -> IssuedCredentials:
        """Mint a bearer credential for the staff member whose session is presenting.

        Deliberately *not* a session-consuming exchange: this is called by the
        Swagger page on load, and logging somebody out of the admin for opening
        the documentation would be a poor trade.

        Staff-only, because "signed into the admin" is the case this exists for,
        and an end user who happens to hold a session cookie is not it.
        """
        from infrastructure.auth.core.flows import record_event
        from infrastructure.auth.core.models import AuthEventType
        from infrastructure.auth.core.sessions import issue_credentials

        user = self._signed_in(request, "No signed-in session to mint a token for.")
        if not user.is_staff:
            raise ApiError(
                "Only staff may mint a token from a session.",
                status=403,
                title=ResponseTitle.FORBIDDEN,
            )
        credentials = issue_credentials(request, user, method=ADMIN_SESSION_METHOD)
        record_event(request, AuthEventType.LOGIN_SUCCEEDED, user=user, method=ADMIN_SESSION_METHOD)
        return credentials

    def _signed_in(self, request: HttpRequest, missing: str) -> Any:
        if settings.AUTH_TOKEN_MODE == "none":
            # Nothing to exchange for: under this mode the session cookie *is*
            # the credential the API accepts.
            raise ApiError(
                "This deployment issues no bearer tokens.",
                status=409,
                title=ResponseTitle.TOKENS_UNSUPPORTED,
            )
        user = getattr(request, "user", None)
        if user is None or not user.is_authenticated:
            raise ApiError(missing, status=401, title=ResponseTitle.NOT_SIGNED_IN)
        # Django's own backend already refuses to resolve a session belonging to
        # a deactivated account, so this is belt-and-braces for a project that
        # has swapped AUTHENTICATION_BACKENDS for one less careful.
        if not user.is_active:
            raise ApiError(
                "This account is disabled.", status=401, title=ResponseTitle.ACCOUNT_DISABLED
            )
        return user


session_exchange_service = SessionExchangeService()
