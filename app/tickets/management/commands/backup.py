"""
Back up from the command line (also used by the Windows safety-net task).

  manage.py backup                       back up to every destination that is switched on
  manage.py backup --dest <id>           one destination
  manage.py backup --full-to D:\\Copies   a single-file full copy into a folder
  manage.py backup --if-overdue          only if a destination has missed its schedule
  manage.py backup --list                show destinations and their backups
"""
from django.core.management.base import BaseCommand, CommandError

from tickets import state
from tickets.backup import destinations as dests
from tickets.backup import engine
from tickets.backup.runner import Runner, dest_state, load_state, human_size


class Command(BaseCommand):
    help = "Make a DiagTrack backup now."

    def add_arguments(self, parser):
        parser.add_argument("--dest", default="")
        parser.add_argument("--full-to", default="")
        parser.add_argument("--if-overdue", action="store_true")
        parser.add_argument("--list", action="store_true")

    def say(self, percent, step):
        self.stdout.write(f"  {'' if percent is None else f'{int(percent):3d}% '}{step}")

    def handle(self, *args, **o):
        cfg = dests.load_config()
        if o["list"]:
            for d in cfg["destinations"]:
                ds = dest_state(load_state(), d["id"])
                self.stdout.write(f"{d['id']}  {d['name']}  {d['path']}  {'on' if d['enabled'] else 'off'}  "
                                  f"{dests.describe(d)}  last good: {ds.get('last_ok', 'never')}")
                try:
                    for e in engine.catalog(dests.prepare(d))[:10]:
                        self.stdout.write(f"     {e['name']}  {human_size(e['size'])}")
                except Exception as exc:
                    self.stdout.write(f"     (cannot read: {exc})")
            return
        if not state.take_lock("backup"):
            raise CommandError("Another backup is running right now. Try again in a few minutes.")
        try:
            if o["full_to"]:
                out = engine.create(kind="full", full_to=o["full_to"], progress=self.say)
                self.stdout.write(self.style.SUCCESS(f"Full copy saved: {out['file']} ({human_size(out['size'])})"))
                self.stdout.write(f"SHA-256: {out['sha256']}")
                return
            targets = [d for d in cfg["destinations"] if d.get("enabled") and (not o["dest"] or d["id"] == o["dest"])]
            if not targets:
                raise CommandError("No matching backup destination is switched on.")
            runner, failed = Runner(), 0
            for d in targets:
                if o["if_overdue"]:
                    last_ok = state.parse_iso(dest_state(load_state(), d["id"]).get("last_ok"))
                    if last_ok and last_ok >= dests.last_slot(d):
                        self.stdout.write(f"{d['name']}: up to date.")
                        continue
                self.stdout.write(f"{d['name']} ({d['path']}):")
                r = runner.backup_to(d, "scheduled" if o["if_overdue"] else "manual", self.say)
                if r["ok"]:
                    self.stdout.write(self.style.SUCCESS(f"  Saved {r['name']} ({r['size_text']})"))
                else:
                    failed += 1
                    self.stderr.write(self.style.ERROR(f"  Failed: {r['message']}"))
            if failed:
                raise CommandError(f"{failed} backup(s) failed. See the messages above.")
        finally:
            state.release_lock("backup")
