"""What the club announces, so other apps can react without this one importing them.

Sent after the transaction commits, for the reason every after-commit hook
exists: a receiver that fired inside the transaction could congratulate somebody
on a level-up that then rolled back.

The payloads are plain dictionaries rather than model instances, so a receiver
does not have to import this app's models or know which of them is the
authoritative one. `level_changed` is the one an integration actually wants --
it is the moment there is something to tell a member about.
"""

import logging
from typing import Any

import django.dispatch
from django.db import transaction

logger = logging.getLogger(__name__)

#: An account joined a club. ``membership``.
member_joined = django.dispatch.Signal()

#: An account left, or was removed. ``membership``.
member_left = django.dispatch.Signal()

#: XP was paid. ``membership``, ``award``.
xp_awarded = django.dispatch.Signal()

#: A member moved between rungs. ``membership``, ``previous``, ``current``.
#: Sent on the way down as well as up, because XP can be taken back by an
#: operator reversing an award and a badge that only ever goes up would lie.
level_changed = django.dispatch.Signal()

#: A mission was finished. ``membership``, ``mission``, ``award``.
mission_completed = django.dispatch.Signal()


def announce(signal: django.dispatch.Signal, **payload: Any) -> None:
    """Send one signal once the work that caused it is really committed."""
    transaction.on_commit(lambda: _send(signal, payload))


def _send(signal: django.dispatch.Signal, payload: dict[str, Any]) -> None:
    """Deliver to every receiver: one broken listener must not fail a join or stop the rest."""
    for receiver, outcome in signal.send_robust(sender=None, **payload):
        if isinstance(outcome, Exception):
            logger.error(
                "A club signal receiver failed: %r",
                receiver,
                exc_info=(type(outcome), outcome, outcome.__traceback__),
            )
