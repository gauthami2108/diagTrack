"""
Restore from the command line, for when the screens cannot be opened.

  manage.py restore <file.dtbak>                  a full copy, or a backup together with --dest
  manage.py restore --dest <id> --name <file>     a backup from a destination folder
  manage.py restore --dest <id>                   the newest backup in that folder
"""
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from tickets import state
from tickets.backup import destinations as dests
from tickets.backup import engine


class Command(BaseCommand):
    help = "Restore DiagTrack from a backup. Everything entered after the backup was made is replaced."

    def add_arguments(self, parser):
        parser.add_argument("file", nargs="?", default="")
        parser.add_argument("--dest", default="")
        parser.add_argument("--name", default="")
        parser.add_argument("--yes", action="store_true", help="Do not ask for confirmation.")

    def say(self, percent, step):
        self.stdout.write(f"  {'' if percent is None else f'{int(percent):3d}% '}{step}")

    def handle(self, *args, **o):
        cfg = dests.load_config()
        root = None
        if o["dest"]:
            dest = dests.find(cfg, o["dest"])
            if not dest:
                raise CommandError("No destination with that id. See: manage.py backup --list")
            root = dests.prepare(dest)
        if o["file"]:
            path = Path(o["file"])
        elif root:
            entries = engine.catalog(root)
            if o["name"]:
                path = root / "backups" / Path(o["name"]).name
            elif entries:
                path = Path(entries[0]["path"])
            else:
                raise CommandError("There is no backup in that folder.")
        else:
            raise CommandError("Name a backup file, or a destination with --dest.")
        if not path.exists():
            raise CommandError(f"{path} was not found.")
        s = engine.container.summary_of(path)
        self.stdout.write(f"Backup: {path.name}\n  made {str(s.get('created'))[:19].replace('T', ' ')} UTC by DiagTrack {s.get('app_version')} "
                          f"on {s.get('platform')}\n  {s.get('counts', {}).get('tickets', '?')} tickets")
        if not o["yes"]:
            answer = input("This replaces ALL current DiagTrack data. Type YES to continue: ")
            if answer.strip() != "YES":
                self.stdout.write("Cancelled. Nothing changed.")
                return
        if not state.take_lock("backup"):
            raise CommandError("A backup is running right now. Try again in a few minutes.")
        state.set_maintenance("A backup is being restored.")
        try:
            res = engine.restore(str(path), root, self.say)
        except Exception as exc:
            raise CommandError(f"The restore failed and the live data was not changed: {exc}") from exc
        finally:
            state.clear_maintenance()
            state.release_lock("backup")
        self.stdout.write(self.style.SUCCESS(
            f"Restored. {res['counts'].get('tickets', 0)} tickets, {res['attachments_restored']} attachment(s) copied. "
            f"The previous database is kept as {res['previous_database']}."))
