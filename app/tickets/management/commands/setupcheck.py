"""Used by setup to check an installation. Prints one line of JSON."""
import json
import os

from django.conf import settings
from django.core.management.base import BaseCommand
from django.utils import timezone

from tickets import state
from tickets.models import Heartbeat, Ticket, User


class Command(BaseCommand):
    help = "Check the database, the worker and the administrator login (used by setup)."

    def add_arguments(self, parser):
        parser.add_argument("--admin", default="")

    def handle(self, *args, **o):
        out = {"db": True, "version": settings.DIAGTRACK_VERSION, "tickets": Ticket.objects.count(),
               "admins": User.objects.filter(role=User.ADMIN, is_active=True).count()}
        beat = Heartbeat.objects.filter(name="worker").first()
        out["worker_age"] = int((timezone.now() - beat.at).total_seconds()) if beat else None
        cat = state.read_json(state.status_dir() / "backup-catalog.json", {}) or {}
        updated = state.parse_iso(cat.get("updated"))
        out["runner_age"] = int((timezone.now() - updated).total_seconds()) if updated else None
        if o["admin"]:
            user = User.objects.filter(username=o["admin"]).first()
            out["admin_ok"] = bool(user and user.check_password(os.environ.get("ADMIN_CHECK_PASSWORD", "")))
        self.stdout.write(json.dumps(out))
