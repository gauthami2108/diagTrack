from datetime import timedelta
from django.core.management.base import BaseCommand
from django.utils import timezone
from tickets.models import Status, Ticket
from tickets.services import log


class Command(BaseCommand):
    help = "Archive closed or cancelled tickets older than a given number of months."

    def add_arguments(self, parser):
        parser.add_argument("--months", type=int, default=12, help="Age in months (default: 12)")
        parser.add_argument("--days", type=int, default=None, help="Age in days (overrides --months if set)")
        parser.add_argument("--dry-run", action="store_true", help="Report tickets without modifying database")

    def handle(self, *args, **options):
        now = timezone.now()
        days = options["days"] or (options["months"] * 30)
        cutoff = now - timedelta(days=days)
        dry_run = options["dry_run"]

        qs = Ticket.objects.filter(
            status__in=[Status.CLOSED, Status.CANCELLED],
            closed_at__lte=cutoff,
            is_archived=False,
        )
        count = qs.count()
        if dry_run:
            self.stdout.write(f"Would archive {count} ticket(s) closed before {cutoff:%Y-%m-%d}.")
            return

        archived = 0
        for t in qs:
            t.is_archived = True
            t.archived_at = now
            t.save(update_fields=["is_archived", "archived_at", "updated_at"])
            log(t, None, f"Archived historical ticket (older than {days} days)")
            archived += 1

        self.stdout.write(self.style.SUCCESS(f"Successfully archived {archived} ticket(s)."))
