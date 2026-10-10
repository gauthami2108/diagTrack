"""Docker: the backup container runs this. Native Windows runs the same runner inside `serve`."""
import logging
import time

from django.core.management.base import BaseCommand

from tickets.backup.runner import Runner

log = logging.getLogger("diagtrack.backup")


class Command(BaseCommand):
    help = "Run scheduled backups and the jobs asked for on the Backups page."

    def handle(self, *args, **o):
        log.info("Backup runner started.")
        runner = Runner()
        while True:
            runner.safe_tick()
            time.sleep(5)
