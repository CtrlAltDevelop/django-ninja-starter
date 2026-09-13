"""This app's own sidebar group and dashboard numbers.

The wallet earns a place on the front page for the same reason the support desk
does: its work is somebody *waiting*. A deposit sitting unapplied is a customer
who has paid and cannot spend, and that gets worse every hour -- which is exactly
what a dashboard is for and exactly what a list of models never says.

The three numbers are chosen to be the three questions an operator opens this
page to ask. What is waiting on me? Is anything about to be paid out? And is the
catalogue actually in a state where somebody can pay at all -- because a
deployment with no enabled method has a wallet nobody can put money into, and
that fails silently at the last step of a customer's journey rather than here.

See :mod:`infrastructure.common.adminui` for the protocol.
"""

from typing import Any

from django.conf import settings
from django.db.models import Count, Q, Sum
from django.http import HttpRequest

from infrastructure.common.adminui import Section, card, changelist, item, may

NAVIGATION_ORDER = 50
DASHBOARD_ORDER = 50


def navigation(request: HttpRequest) -> dict[str, Any]:
    """What is waiting leads; what is configured once sits under it."""
    return {
        "title": "Wallet",
        "separator": False,
        "collapsible": False,
        "items": [
            item(
                "Movements",
                "receipt_long",
                changelist("wallet", "walletentry"),
                "wallet.view_walletentry",
            ),
            item(
                "Wallets",
                "account_balance_wallet",
                changelist("wallet", "wallet"),
                "wallet.view_wallet",
            ),
            item(
                "Ways to pay",
                "credit_card",
                changelist("wallet", "paymentmethod"),
                "wallet.view_paymentmethod",
                "wallet.change_paymentmethod",
            ),
            item(
                "Currencies and chains",
                "currency_exchange",
                changelist("wallet", "methodcurrency"),
                "wallet.view_methodcurrency",
            ),
            item("Fees", "percent", changelist("wallet", "methodfee"), "wallet.view_methodfee"),
            item(
                "Exchange rates",
                "sync_alt",
                changelist("wallet", "exchangerate"),
                "wallet.view_exchangerate",
            ),
            item(
                "Checkpoints",
                "inventory",
                changelist("wallet", "walletcheckpoint"),
                "wallet.view_walletcheckpoint",
            ),
        ],
    }


def wallet_numbers() -> dict[str, Any]:
    """The three figures the dashboard asks for, in as few queries as they take."""
    from apps.wallet.catalog import PaymentMethod
    from apps.wallet.models import EntryStatus, WalletEntry
    from apps.wallet.money import written

    waiting = WalletEntry.objects.awaiting_approval()
    payouts = Q(direction="debit")
    outstanding = WalletEntry.objects.filter(status=str(EntryStatus.PENDING)).aggregate(
        owed=Sum("amount", filter=payouts),
        count=Count("id", filter=payouts),
    )
    live = PaymentMethod.objects.usable().filter(currencies__is_enabled=True).distinct()
    currency = settings.WALLET_CURRENCY
    return {
        "waiting": waiting.count(),
        "waiting_value": written(waiting.aggregate(total=Sum("amount"))["total"], currency),
        "payouts": outstanding["count"],
        "payouts_value": written(outstanding["owed"], currency),
        "methods": live.count(),
    }


def dashboard(request: HttpRequest) -> Section | None:
    """Three numbers, and the first one is somebody waiting on a person."""
    if not may(request, "wallet.view_walletentry"):
        return None

    numbers = wallet_numbers()
    queue = changelist("wallet", "walletentry")
    return Section(
        title="Wallet",
        cards=[
            card(
                "Waiting to be applied",
                numbers["waiting"],
                hint=f"{numbers['waiting_value']} held up"
                if numbers["waiting"]
                else "nothing is waiting on a person",
                icon="pending_actions",
                link=queue,
                tone="warn" if numbers["waiting"] else "good",
            ),
            card(
                # The count, not the sum: every other figure on this page is a
                # number of things, and a bare decimal in the slot beside them
                # reads as a broken card rather than as money. What it is worth
                # goes in the hint, written the way money is written.
                "Payouts not yet settled",
                numbers["payouts"],
                hint=f"{numbers['payouts_value']} promised, and already out of every"
                " available balance"
                if numbers["payouts"]
                else "nothing is promised and unpaid",
                icon="payments",
                link=queue,
            ),
            card(
                "Ways to pay",
                numbers["methods"],
                # The one that is bad at zero rather than good: no usable method
                # means a wallet nobody can put money into, and the customer
                # finds that out at the last step instead of an operator finding
                # it out here.
                hint="enabled, with a currency configured"
                if numbers["methods"]
                else "nobody can pay in or out until one is configured",
                icon="credit_card",
                link=changelist("wallet", "paymentmethod"),
                tone="good" if numbers["methods"] else "bad",
            ),
        ],
    )
