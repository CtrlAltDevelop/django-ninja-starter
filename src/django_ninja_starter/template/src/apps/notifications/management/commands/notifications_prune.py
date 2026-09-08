"""Delete notifications older than the retention window.

Retention is a scheduled command rather than something the app does on its own,
because deleting rows on a timer nobody asked for is the kind of surprise a
starter should not ship. ``DJANGO_NOTIFICATIONS_RETENTION_DAYS`` defaults to
zero -- keep everything -- so a project that never runs this never loses
history, and one that does run it says how long out loud.

    manage.py notifications_prune            # the configured window
    manage.py notifications_prune --days 90  # override it
    manage.py notifications_prune --dry-run  # count without deleting
"""

from datetime import timedelta
from typing import Any

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from apps.notifications.models import Notification, prune


class Command(BaseCommand):
    help = "Delete notifications older than the retention window, receipts included."

    def add_arguments(self, parser: Any) -> None:
        parser.add_argument(
            "--days",
            type=int,
            default=None,
            help="Override DJANGO_NOTIFICATIONS_RETENTION_DAYS for this run.",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Say how many would go, and delete nothing.",
        )

    def handle(self, *args: Any, **options: Any) -> None:
        days = options["days"]
        if days is None:
            days = settings.NOTIFICATIONS_RETENTION_DAYS
        if days <= 0:
            raise CommandError(
                "No retention window: set DJANGO_NOTIFICATIONS_RETENTION_DAYS "
                "or pass --days. Refusing to guess how much history to delete."
            )

        cutoff = timezone.now() - timedelta(days=days)
        if options["dry_run"]:
            count = Notification.objects.filter(created_at__lt=cutoff).count()
            self.stdout.write(
                f"Would delete {count} notification(s) created before {cutoff:%Y-%m-%d %H:%M}."
            )
            return

        # Counted before the delete, because `prune` reports rows across every
        # cascaded table and "3 deleted" should mean three notifications.
        count = Notification.objects.filter(created_at__lt=cutoff).count()
        prune(cutoff)
        self.stdout.write(
            self.style.SUCCESS(
                f"Deleted {count} notification(s) created before {cutoff:%Y-%m-%d %H:%M}."
            )
        )
