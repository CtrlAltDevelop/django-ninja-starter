"""Give up on movements that waited on their rail longer than the window.

Run hourly, or daily. A deposit whose webhook never came stays pending for ever
without this, and a pending payout holds its money out of ``available`` for ever
with it -- a customer with money they cannot spend and nothing on the page to say
why.

The window is ``DJANGO_WALLET_EXPIRE_AFTER_HOURS``, and zero means this does
nothing: the right number is a fact about the slowest rail a deployment runs,
not one this app can pick. A request waiting on an operator is never expired
here -- that wait is a person's, and emptying the queue is not a fix for nobody
reading it.
"""

from datetime import timedelta
from typing import Any

from django.core.management.base import BaseCommand, CommandError

from apps.wallet.services import wallet_service


class Command(BaseCommand):
    help = "Expire pending wallet movements that nothing confirmed inside the window."

    def add_arguments(self, parser: Any) -> None:
        parser.add_argument(
            "--older-than-hours",
            type=int,
            default=None,
            help=(
                "Use this window instead of DJANGO_WALLET_EXPIRE_AFTER_HOURS, for a "
                "one-off sweep. 0 expires everything pending that is not awaiting an "
                "operator."
            ),
        )

    def handle(self, *args: Any, **options: Any) -> None:
        hours = options["older_than_hours"]
        if hours is not None and hours < 0:
            raise CommandError("--older-than-hours cannot be negative.")
        result = wallet_service.expire_stale(
            older_than=timedelta(hours=hours) if hours is not None else None
        )
        if result.window is None:
            self.stdout.write(
                "Expiry is off: DJANGO_WALLET_EXPIRE_AFTER_HOURS is 0, so nothing was "
                "expired. Set a window longer than your slowest rail takes to confirm."
            )
            return
        hours_shown = int(result.window.total_seconds() // 3600)
        self.stdout.write(
            self.style.SUCCESS(
                f"Expired {result.expired} movements pending for more than {hours_shown}h."
            )
        )
        if result.awaiting_operator:
            # Not expired, and said out loud: a stale request is a queue nobody
            # is reading, which this job must not hide by emptying it.
            self.stdout.write(
                f"{result.awaiting_operator} stale requests are waiting on an operator "
                "and were left alone."
            )
