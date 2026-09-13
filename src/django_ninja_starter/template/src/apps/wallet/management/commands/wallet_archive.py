"""Fold settled entries into checkpoints, so reading a balance stays cheap.

Run daily. A balance is the last checkpoint plus every entry written since it,
which means the cost of reading one is the number of entries that have piled up
since the last fold -- and left alone, that number only grows.

Safe to run as often as you like: each wallet is folded only when it is due, and
the whole of one wallet's fold happens inside that wallet's row lock, so it can
run beside live traffic rather than in a window.
"""

from typing import Any

from django.core.management.base import BaseCommand

from apps.wallet.balances import archive_after, archive_all, archive_threshold


class Command(BaseCommand):
    help = "Fold settled wallet entries into checkpoints so balances stay cheap to read."

    def add_arguments(self, parser: Any) -> None:
        parser.add_argument(
            "--force",
            action="store_true",
            help=(
                "Fold every wallet that has anything foldable, ignoring the age and "
                "count triggers. Nothing skips the rule that a pending entry is never "
                "archived."
            ),
        )
        parser.add_argument("--quiet", action="store_true", help="Report only the totals.")

    def handle(self, *args: Any, **options: Any) -> None:
        results = archive_all(force=options["force"])
        for result in results:
            if not options["quiet"]:
                checkpoint = result.checkpoint
                self.stdout.write(
                    f"{result.wallet_id}: archived {result.archived} into checkpoint "
                    f"#{checkpoint.sequence if checkpoint else '-'} "
                    f"(balance {checkpoint.balance if checkpoint else '-'})"
                    + (f", left {result.skipped_pending} pending" if result.skipped_pending else "")
                )
        folded = sum(result.archived for result in results)
        waiting = sum(result.skipped_pending for result in results)
        self.stdout.write(
            self.style.SUCCESS(
                f"Folded {folded} entries across {len(results)} wallets "
                f"(threshold {archive_threshold()}, age {archive_after().days}d)."
            )
        )
        if waiting:
            # Not a warning. A pending entry holds its own run back by design --
            # a checkpoint is a number written down, and folding in something
            # still free to change would make that number wrong later.
            self.stdout.write(f"{waiting} pending entries were left for a later run.")
