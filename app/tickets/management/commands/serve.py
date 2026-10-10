"""
Native Windows: the whole of DiagTrack in one process, run by the DiagTrack Windows service.

  - the screens (Waitress web server)
  - the worker (SLA checks, escalations, alerts), every minute
  - the backup runner (schedules, Back up now, restores, checks)

Docker does not use this; it runs the same parts as separate containers.
"""
import logging
import threading
import time

from django.conf import settings
from django.core.management import call_command
from django.core.management.base import BaseCommand

from tickets.worker import Worker

log = logging.getLogger("diagtrack.serve")


def _worker_loop(stop):
    worker = Worker()
    while not stop.is_set():
        worker.safe_round()
        stop.wait(60)


def _backup_loop(stop):
    from tickets.backup.runner import Runner
    runner = Runner()
    while not stop.is_set():
        runner.safe_tick()
        stop.wait(5)


class Command(BaseCommand):
    help = "Run DiagTrack (screens, worker and backups) in one process. Used by the Windows service."

    def add_arguments(self, parser):
        parser.add_argument("--host", default=None)
        parser.add_argument("--port", type=int, default=None)
        parser.add_argument("--threads", type=int, default=8)
        parser.add_argument("--no-migrate", action="store_true")
        parser.add_argument("--no-backups", action="store_true")

    def handle(self, *args, **opts):
        import os

        from waitress import serve

        from config.wsgi import application

        host = opts["host"] or os.environ.get("LISTEN_HOST", "0.0.0.0")
        port = opts["port"] or int(os.environ.get("HTTP_PORT", "8080"))
        if not opts["no_migrate"]:
            log.info("Preparing the database...")
            call_command("migrate", interactive=False, verbosity=0)
            call_command("setup_initial", verbosity=0)
        stop = threading.Event()
        threading.Thread(target=_worker_loop, args=(stop,), name="worker", daemon=True).start()
        if not opts["no_backups"]:
            threading.Thread(target=_backup_loop, args=(stop,), name="backups", daemon=True).start()
        log.info("DiagTrack %s listening on http://%s:%s", settings.DIAGTRACK_VERSION, host, port)
        try:
            serve(application, host=host, port=port, threads=opts["threads"], ident="DiagTrack",
                  max_request_body_size=64 * 1024 * 1024, channel_timeout=120)
        finally:
            stop.set()
            time.sleep(0.5)
