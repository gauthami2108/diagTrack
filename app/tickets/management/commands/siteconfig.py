"""Used by setup: save the address of this system (email links and QR labels)."""
from django.core.management.base import BaseCommand

from tickets.models import SiteSettings


class Command(BaseCommand):
    help = "Set organisation settings from setup."

    def add_arguments(self, parser):
        parser.add_argument("--site-url", default="")

    def handle(self, *args, **o):
        s = SiteSettings.load()
        if o["site_url"]:
            s.site_url = o["site_url"].rstrip("/")
        s.save()
        self.stdout.write(f"Address saved: {s.site_url}")
