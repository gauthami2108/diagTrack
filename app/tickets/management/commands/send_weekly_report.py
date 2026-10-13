from django.core.management.base import BaseCommand
from tickets import reports


class Command(BaseCommand):
    help = "Generate and send the weekly management summary email (missed SLAs, downtime, open tickets)."

    def add_arguments(self, parser):
        parser.add_argument("--force", action="store_true", help="Send even if not Monday or already sent today.")

    def handle(self, *args, **options):
        sent = reports.send_weekly_management_report(force=options["force"])
        if sent:
            self.stdout.write(self.style.SUCCESS(f"Successfully queued {sent} weekly report email(s)."))
        else:
            self.stdout.write("No report sent (either disabled, already sent today, or no management recipients). Use --force to override.")
