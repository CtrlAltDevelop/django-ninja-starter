"""What happens between "the first factor checked out" and "here is your token".

Every login method funnels through :func:`complete_login`, which is the only
place that decides whether an account still owes a second factor. Adding a new
method therefore cannot accidentally skip 2FA.
"""

from dataclasses import dataclass, field
from typing import Any

from django.apps import apps
from django.conf import settings
from django.contrib.auth import get_user_model
from django.http import HttpRequest

from infrastructure.auth.core.challenges import Challenge, get_challenge_store
from infrastructure.auth.core.errors import AuthError
from infrastructure.auth.core.models import AuthEvent, AuthEventType
from infrastructure.auth.core.sessions import (
    IssuedCredentials,
    client_ip,
    issue_credentials,
    user_agent,
)
from infrastructure.auth.core.throttling import fingerprint
from infrastructure.common.app_labels import app_installed

PENDING_PURPOSE = "second_factor"
TWO_FACTOR_APP = "auth_twofactor"


@dataclass(frozen=True)
class LoginResult:
    """Either a finished login or the ticket that stands in for one."""

    credentials: IssuedCredentials | None = None
    pending_ticket: str = ""
    methods: list[str] = field(default_factory=list)

    @property
    def requires_second_factor(self) -> bool:
        return self.credentials is None


def record_event(
    request: HttpRequest,
    event_type: str,
    *,
    user: Any | None = None,
    method: str = "",
    identifier: str = "",
    **metadata: Any,
) -> None:
    """Append one row to the sign-in audit trail."""
    AuthEvent.objects.create(
        event_type=event_type,
        method=method,
        user=user,
        identifier_hash=fingerprint(identifier) if identifier else "",
        ip_address=client_ip(request),
        user_agent=user_agent(request),
        metadata=metadata,
    )


def enrolled_second_factors(user: Any) -> list[str]:
    """Return the confirmed second factors for an account, newest enrolment last."""
    if not app_installed(TWO_FACTOR_APP):
        return []
    factor_model = apps.get_model("auth_twofactor", "SecondFactor")
    return list(
        factor_model.objects.filter(user=user, confirmed_at__isnull=False)
        .order_by("created_at")
        .values_list("method", flat=True)
    )


def complete_login(
    request: HttpRequest,
    user: Any,
    *,
    method: str,
    identifier: str = "",
) -> LoginResult:
    """Finish a login, or park it until the account clears a second factor."""
    if not user.is_active:
        raise AuthError("This account is disabled.", status=403)
    factors = enrolled_second_factors(user)
    if not factors:
        credentials = issue_credentials(request, user, method=method)
        record_event(
            request,
            AuthEventType.LOGIN_SUCCEEDED,
            user=user,
            method=method,
            identifier=identifier,
        )
        return LoginResult(credentials=credentials)

    ticket = get_challenge_store().create(
        purpose=PENDING_PURPOSE,
        subject=str(user.pk),
        metadata={"methods": factors, "first_factor": method},
        ttl=settings.AUTH_PENDING_LOGIN_TTL_SECONDS,
    )
    record_event(
        request,
        AuthEventType.SECOND_FACTOR_REQUIRED,
        user=user,
        method=method,
        identifier=identifier,
        methods=factors,
    )
    return LoginResult(pending_ticket=ticket, methods=factors)


def resolve_pending_login(ticket: str) -> tuple[Any, dict[str, Any]]:
    """Recover the account behind a pending-login ticket without consuming it.

    Reading rather than consuming is what lets a user mistype an authenticator
    code without being thrown back to the password prompt.
    """
    store = get_challenge_store()
    challenge = store.read(ticket, purpose=PENDING_PURPOSE)
    user = get_user_model()._default_manager.filter(pk=challenge.subject).first()
    if user is None or not user.is_active:
        store.discard(ticket)
        raise AuthError("This sign-in is no longer valid. Start again.", status=400)
    return user, dict(challenge.metadata)


def pending_methods(metadata: dict[str, Any]) -> list[str]:
    return [str(item) for item in (metadata.get("methods") or [])]


def consume_codeless(ticket: str, purpose: str) -> Challenge:
    """Read and immediately retire a ticket that is its own secret."""
    store = get_challenge_store()
    challenge = store.read(ticket, purpose=purpose)
    store.discard(ticket)
    return challenge


def send_code_challenge(
    request: HttpRequest,
    *,
    purpose: str,
    subject: str,
    channel: str,
    destination: str,
    method: str,
    intro: str = "Your sign-in code",
) -> str:
    """Mint a code, send it, and return the ticket that redeems it."""
    from infrastructure.auth.core.codes import generate_numeric_code
    from infrastructure.auth.core.delivery import send_email, send_sms
    from infrastructure.auth.core.throttling import guard_delivery

    guard_delivery(f"{method}:{channel}", destination)
    code = generate_numeric_code()
    ticket = get_challenge_store().create(
        purpose=purpose,
        subject=subject,
        code=code,
        channel=channel,
        destination=destination,
    )
    body = f"{intro}: {code}"
    if channel == "sms":
        send_sms(destination, body)
    else:
        send_email(destination, intro, body)
    record_event(
        request,
        AuthEventType.CODE_SENT,
        method=method,
        identifier=destination,
        channel=channel,
        purpose=purpose,
    )
    return ticket


def decoy_challenge(purpose: str, *, channel: str = "", destination: str = "") -> str:
    """Return a ticket nobody can satisfy.

    Flows that must not reveal whether an address is registered still have to
    answer with a ticket, or the shape of the response becomes the answer. This
    mints one bound to a code that was never sent anywhere.
    """
    from infrastructure.auth.core.codes import generate_numeric_code

    return get_challenge_store().create(
        purpose=purpose,
        subject="",
        code=generate_numeric_code(),
        channel=channel,
        destination=destination,
    )


def user_from_challenge(challenge: Any) -> Any:
    """Resolve the account a settled challenge was issued for."""
    user = (
        get_user_model()._default_manager.filter(pk=challenge.subject).first()
        if challenge.subject
        else None
    )
    if user is None:
        raise AuthError("That code is not valid.", status=400)
    return user
