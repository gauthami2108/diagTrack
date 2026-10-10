"""
Check a backup without restoring it, or test-restore it into a scratch database.

  manage.py verifybackup <file.dtbak>
  manage.py verifybackup --dest <id> [--name <file>] [--test-restore]
"""
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from tickets.backup import destinations as dests
from tickets.backup import engine


class Command(BaseCommand):
    help = "Check that a backup is intact, and optionally that it restores."

    def add_arguments(self, parser):
        parser.add_argument("file", nargs="?", default="")
        parser.add_argument("--dest", default="")
        parser.add_argument("--name", default="")
        parser.add_argument("--test-restore", action="store_true")

    def handle(self, *args, **o):
        root = None
        if o["dest"]:
            dest = dests.find(dests.load_config(), o["dest"])
            if not dest:
                raise CommandError("No destination with that id.")
            root = dests.prepare(dest)
        if o["file"]:
            path = Path(o["file"])
        elif root:
            entries = engine.catalog(root)
            if not entries:
                raise CommandError("There is no backup in that folder.")
            path = root / "backups" / o["name"] if o["name"] else Path(entries[0]["path"])
        else:
            raise CommandError("Name a backup file, or a destination with --dest.")
        try:
            res = engine.verify(str(path), root)
            self.stdout.write(self.style.SUCCESS(f"{path.name}: intact, {res['attachments']} attachment(s) present."))
            if o["test_restore"]:
                t = engine.test_restore(str(path), root, lambda p, s: self.stdout.write(f"  {s}"))
                self.stdout.write(self.style.SUCCESS(f"Test restore passed: {t['counts']}"))
        except Exception as exc:
            raise CommandError(f"{path.name}: {exc}") from exc
