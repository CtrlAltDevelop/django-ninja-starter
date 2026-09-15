"""What the shop announces, so other apps can react without the shop importing them.

Sent after the transaction commits, for the reason every after-commit hook
exists: a receiver that ran inside it could reward somebody for an order whose
payment then rolled back. The payloads are plain dictionaries, so a receiver
needs none of this app's models to read one.

Connect to them the way :mod:`apps.club.bridges.shop` does::

    from apps.shop import signals

    signals.order_paid.connect(on_paid, dispatch_uid="myapp.order_paid")
"""

import logging
from typing import Any

from django.db import transaction
from django.dispatch import Signal

logger = logging.getLogger(__name__)

#: An order's payment settled. ``order``: ``id``, ``number``, ``user_id``,
#: ``total``, ``currency`` and ``items``, the number of lines it had. Sent once
#: per order: settling one that is already paid announces nothing.
order_paid = Signal()

#: A review became public -- written while moderation is off, or approved by a
#: moderator. ``review``: ``id``, ``user_id``, ``product_id`` and ``rating``.
#: An edited review that is approved again is announced again, under the same id.
review_published = Signal()


def announce(signal: Signal, **payload: Any) -> None:
    """Send ``signal`` once the current transaction commits, and not before."""
    transaction.on_commit(lambda: _send(signal, payload))


def _send(signal: Signal, payload: dict[str, Any]) -> None:
    """Deliver to every receiver, so one broken listener cannot fail a checkout."""
    for receiver, outcome in signal.send_robust(sender="apps.shop", **payload):
        if isinstance(outcome, Exception):
            logger.error(
                "A shop signal receiver failed: %r",
                receiver,
                exc_info=(type(outcome), outcome, outcome.__traceback__),
            )
