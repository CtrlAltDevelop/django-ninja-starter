"""What the wallet emits, wired to the signals it already sends.

The wallet grew an after-commit signal for every movement, so this bridge needs
no changes at the wallet's call sites at all: it connects a receiver and turns
each settled movement into an occurrence. That is the pattern to copy for an app
of your own that already has signals.
"""

from typing import Any

from apps.club.events import EventSpec, register

register(
    EventSpec(
        key="wallet.deposit.settled",
        label="A deposit settled",
        description="Sent when money really arrives, not when it is recorded.",
        value_label="The amount that landed, in the wallet's currency",
        fields={"method": "The rail it came in on", "currency": "The wallet's currency"},
        source="wallet",
    )
)

register(
    EventSpec(
        key="wallet.withdrawal.settled",
        label="A payout settled",
        value_label="The amount that left",
        fields={"method": "The rail it went out on"},
        source="wallet",
    )
)


def connect() -> None:
    """Listen to the wallet's own signals. Called from the app's `ready`."""
    from apps.wallet import signals as wallet_signals

    wallet_signals.entry_settled.connect(_on_settled, dispatch_uid="club.wallet.settled")


def _on_settled(sender: Any, **kwargs: Any) -> None:
    """Turn a settled movement into an occurrence, if it is a kind we declared.

    The entry is re-read rather than taken from the payload: the wallet's
    signal carries the movement, not the account behind it, and guessing at a
    field it does not publish would break the first time that payload changed.
    """
    from apps.club import track
    from apps.wallet.models import WalletEntry

    entry = kwargs.get("entry") or {}
    keys = {"deposit": "wallet.deposit.settled", "withdrawal": "wallet.withdrawal.settled"}
    key = keys.get(entry.get("kind", ""))
    if key is None:
        return
    row = WalletEntry.objects.select_related("wallet__user").filter(pk=entry.get("id")).first()
    if row is None:
        return
    track(
        row.wallet.user,
        key,
        value=float(row.amount),
        metadata={"method": entry.get("method", ""), "currency": row.wallet.currency},
        reference=f"entry:{row.pk}",
    )
