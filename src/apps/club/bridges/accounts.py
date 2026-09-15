"""Events every deployment has, because every deployment has accounts.

The floor of the registry: with no optional app installed at all, a club can
still be built out of signing in, which is enough for a streak -- and out of
joining, which this app emits itself.

The sign-in trail is where these come from. Every login method, social callback
and second factor already writes one :class:`AuthEvent` row when it succeeds, so
this listens for that row rather than asking each method to call ``track``: a
method added next year is covered without being edited.

``accounts.user.registered`` is heard from the user table instead, because an
account arrives from more places than a login method -- a social callback, the
admin, ``createsuperuser``. An account is in no club at the moment it exists, so
``DJANGO_CLUB_JOIN_ON_SIGNUP`` names a club to put it in first; without it, a
mission on that event has nobody to pay.
"""

import logging
from functools import partial
from typing import Any

from django.db import transaction

from apps.club.events import EventSpec, register

logger = logging.getLogger(__name__)

register(
    EventSpec(
        key="accounts.user.registered",
        label="An account was created",
        description=(
            "Sent once, the first time an account exists, however it arrived. Pays "
            "only when DJANGO_CLUB_JOIN_ON_SIGNUP has put the account in a club."
        ),
        source="accounts",
    )
)

register(
    EventSpec(
        key="accounts.user.signed_in",
        label="An account signed in",
        description="Sent on every successful sign-in, whichever method was used.",
        fields={"method": "password, email_code, sms_code, magic_link, oauth"},
        source="accounts",
    )
)

register(
    EventSpec(
        key="club.member.joined",
        label="A member joined the club",
        description=(
            "Sent by this app itself when somebody joins, so 'welcome' missions "
            "need nothing else installed."
        ),
        source="club",
    )
)


def connect() -> None:
    """Listen for new accounts, and to the sign-in audit trail where it is installed.

    Called from `ready`.
    """
    from django.apps import apps
    from django.conf import settings
    from django.db.models.signals import post_save

    post_save.connect(
        _on_account_saved, sender=settings.AUTH_USER_MODEL, dispatch_uid="club.accounts.created"
    )
    try:
        event_model = apps.get_model("auth_core", "AuthEvent")
    except LookupError:  # pragma: no cover - only in a project without the auth apps
        return
    post_save.connect(_on_auth_event, sender=event_model, dispatch_uid="club.accounts.auth_event")


def _on_auth_event(sender: Any, instance: Any, created: bool, **kwargs: Any) -> None:
    """Turn a successful sign-in row into an occurrence, once it is committed."""
    from infrastructure.auth.core.models import AuthEventType

    if not created or kwargs.get("raw") or instance.user_id is None:
        return
    if str(instance.event_type) != str(AuthEventType.LOGIN_SUCCEEDED):
        return
    # Robust: a mission failing to pay must never be the reason a login fails.
    transaction.on_commit(
        partial(_track, instance.user_id, "accounts.user.signed_in", instance.method, instance.pk),
        robust=True,
    )


def _on_account_saved(sender: Any, instance: Any, created: bool, **kwargs: Any) -> None:
    """A new account: put it in the signup club if there is one, then say it arrived."""
    if not created or kwargs.get("raw"):
        # `raw` is loaddata replaying a fixture, which carries its own memberships.
        return
    transaction.on_commit(partial(_arrived, instance.pk), robust=True)


def _arrived(user_id: Any) -> None:
    from django.conf import settings
    from django.contrib.auth import get_user_model

    from apps.club import track
    from apps.club.errors import ClubError
    from apps.club.services import club_service

    user = get_user_model().objects.filter(pk=user_id).first()
    if user is None:
        return
    slug = getattr(settings, "CLUB_JOIN_ON_SIGNUP", "")
    if slug:
        try:
            club_service.add_member(user, slug)
        except ClubError as refusal:
            # Logged rather than raised: the account exists either way, and a
            # misnamed club must not be the reason a sign-up fails.
            logger.warning("Could not put a new account in club %r: %s", slug, refusal)
    track(user, "accounts.user.registered", reference=f"registered:{user_id}")


def _track(user_id: Any, key: str, method: str, event_id: Any) -> None:
    from django.contrib.auth import get_user_model

    from apps.club import track

    user = get_user_model().objects.filter(pk=user_id).first()
    if user is None:
        return
    track(user, key, metadata={"method": method}, reference=f"auth-event:{event_id}")
