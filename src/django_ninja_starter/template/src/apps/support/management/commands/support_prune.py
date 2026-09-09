"""Delete what a retention window says is no longer worth keeping.

Nothing in this app deletes anything on its own. A starter that quietly removed
a customer's support history on a timer nobody set up would be doing the one
thing a support desk must never do, so retention is a window somebody configures
and a command somebody schedules -- and until both are done, everything is kept.

Two things are pruned, and only two:

**Closed tickets older than the window.** Only closed ones: a ticket that is
still open is somebody's unanswered question however old it is, and resolved is
the desk's opinion rather than the client's agreement. Messages, attachments and
participants go with them by cascade.

**Staged uploads nobody ever attached.** These have their own, much shorter
window, because they are not history -- they are a file somebody picked and then
changed their mind about, sitting in a bucket. A claimed upload is never
deleted whatever its age: the attachment points at it to keep the claim unique.

The files themselves are left in storage. That is deliberate and is said out
loud here rather than discovered: deleting from a bucket is not transactional,
a half-done sweep is worse than none, and a project with a storage lifecycle
rule already has a better tool for it than a Django command.
"""

from typing import Any

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from apps.support.models import prune, prune_uploads

#: How long an unclaimed upload is kept. Not a setting: nobody has an opinion
#: about this number, and a file staged a week ago is one the sender has
#: forgotten about.
UPLOAD_DAYS = 7


class Command(BaseCommand):
    help = "Delete closed tickets and unattached uploads older than the retention window."

    def add_arguments(self, parser: Any) -> None:
        parser.add_argument(
            "--days",
            type=int,
            default=None,
            help=(
                "Override SUPPORT_RETENTION_DAYS for this run. Zero is refused rather "
                "than treated as 'delete everything'."
            ),
        )
        parser.add_argument(
            "--upload-days",
            type=int,
            default=UPLOAD_DAYS,
            help=f"How long an unattached upload is kept. Default {UPLOAD_DAYS}.",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Report what would go, and delete nothing.",
        )

    def handle(self, *args: Any, **options: Any) -> None:
        from django.conf import settings

        days = options["days"]
        if days is None:
            days = settings.SUPPORT_RETENTION_DAYS
        if not days:
            raise CommandError(
                "No retention window is set, so nothing is old enough to delete. "
                "Set DJANGO_SUPPORT_RETENTION_DAYS, or pass --days."
            )
        if days < 0:
            raise CommandError("A retention window cannot be negative.")

        cutoff = timezone.now() - timezone.timedelta(days=days)
        upload_cutoff = timezone.now() - timezone.timedelta(days=options["upload_days"])

        if options["dry_run"]:
            from apps.support.models import Status, Ticket, Upload

            tickets = Ticket.objects.filter(status=Status.CLOSED, closed_at__lt=cutoff).count()
            uploads = Upload.objects.filter(
                created_at__lt=upload_cutoff, attachment__isnull=True
            ).count()
            self.stdout.write(
                f"Would delete {tickets} closed ticket{'' if tickets == 1 else 's'} "
                f"closed before {cutoff:%Y-%m-%d} and {uploads} "
                f"unattached upload{'' if uploads == 1 else 's'}."
            )
            return

        # Reported as rows rather than as tickets: the cascade takes the
        # messages, attachments and participants with it, and a number that
        # counted only the tickets would look wrong to anybody watching the
        # table sizes.
        deleted = prune(cutoff)
        uploads = prune_uploads(upload_cutoff)
        self.stdout.write(
            self.style.SUCCESS(
                f"Deleted {deleted} row{'' if deleted == 1 else 's'} for tickets closed "
                f"before {cutoff:%Y-%m-%d}, and {uploads} "
                f"unattached upload{'' if uploads == 1 else 's'}."
            )
        )
