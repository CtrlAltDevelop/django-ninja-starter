"""What the wallet tells the rest of the project, once the money has actually moved.

The wallet decides nothing on behalf of anybody else. A notification app wants to
tell a customer their deposit landed; a payout integration wants to hear that a
withdrawal is cleared to send; a ledger export wants every settlement. None of
them belongs in :mod:`apps.wallet.services`, and none of them should have to poll
the entry table to find out -- so the service announces, and whoever cares
listens::

    from django.dispatch import receiver
    from apps.wallet import signals

    @receiver(signals.payout_ready)
    def send_it(sender, entry, **kwargs):
        processor.pay(entry["destination"], entry["net_amount"], ref=entry["id"])

**Sent after the transaction commits, never inside it.** A receiver that emails
"your deposit arrived" from inside the transaction would email it for a
settlement that is then rolled back by the balance check a line later. So every
signal waits for :func:`django.db.transaction.on_commit`, and a movement that did
not happen is never announced.

**A receiver cannot break the movement.** The money has already moved by the
time anybody hears about it, so a receiver that raises is logged and the rest
still run. A payout integration that must not lose an event records it durably
in its own receiver -- this is a notification, not a queue.

Every signal carries ``entry``: the same payload every transport answers with
(see :func:`apps.wallet.services.entry_payload`), so a receiver reads the fields
a client would and cannot reach into a half-saved model.
"""

import logging
from collections.abc import Callable
from typing import Any

from django.db import transaction
from django.dispatch import Signal

logger = logging.getLogger(__name__)

#: A movement was written. Its status says whether it already settled.
entry_recorded = Signal()

#: A movement became ``done``: this is the moment money is real.
entry_settled = Signal()

#: The rail refused it, or an operator marked it failed.
entry_failed = Signal()

#: Called off before it landed, by the account or by refusing its request.
entry_cancelled = Signal()

#: Never confirmed inside the window, and given up on.
entry_expired = Signal()

#: A settled movement was undone. Carries ``entry`` -- the original, now
#: ``reversed`` -- and ``correction``, the opposing entry that took the money back.
entry_reversed = Signal()

#: An operator applied a request.
entry_approved = Signal()

#: An operator refused a request.
entry_rejected = Signal()

#: A withdrawal that nothing stands in front of any more: recorded, cleared of
#: approval, holding its money, and waiting on a rail that has to be told to pay.
#: The hook a payout integration listens on. It settles the movement later,
#: through the signed webhook, once the processor confirms the money left.
payout_ready = Signal()

#: A wallet was frozen, unfrozen or closed. Carries ``wallet`` -- its id, its
#: new status and the one it had -- rather than ``entry``.
wallet_status_changed = Signal()

#: Which signal a status change announces, so the service has one lookup rather
#: than a branch per verb.
STATUS_SIGNALS: dict[str, Signal] = {
    "done": entry_settled,
    "failed": entry_failed,
    "cancelled": entry_cancelled,
    "expired": entry_expired,
}


def announce(signal: Signal, **payload: Any) -> None:
    """Send ``signal`` once the current transaction commits, and not before."""
    transaction.on_commit(_sender(signal, payload))


def _sender(signal: Signal, payload: dict[str, Any]) -> Callable[[], None]:
    def send() -> None:
        for receiver, outcome in signal.send_robust(sender="apps.wallet", **payload):
            if isinstance(outcome, Exception):
                logger.error(
                    "A wallet signal receiver failed: %r",
                    receiver,
                    exc_info=(type(outcome), outcome, outcome.__traceback__),
                )

    return send
