"""Send every webhook delivery that is due. Schedule it, e.g. every minute from cron."""

from typing import Any

from django.core.management.base import BaseCommand, CommandParser

from apps.webhooks.services import deliver_due


class Command(BaseCommand):
    help = "Attempt every pending webhook delivery whose next attempt is due."

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument("--limit", type=int, default=100)

    def handle(self, *args: Any, **options: Any) -> None:
        count = deliver_due(limit=options["limit"])
        self.stdout.write(f"Attempted {count} webhook deliveries.")
