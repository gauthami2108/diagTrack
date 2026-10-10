"""
Background worker. Runs forever inside the 'worker' container.
Every minute it: checks SLA deadlines, fires escalations, closes old resolved
tickets and sends waiting email / SMS / WhatsApp alerts.
"""
import logging
import time

from django.core.management.base import BaseCommand

from tickets.worker import Worker

log = logging.getLogger("diagtrack.worker")


class Command(BaseCommand):
    help = "Run the DiagTrack background worker."

    def add_arguments(self, parser):
        parser.add_argument("--once", action="store_true", help="Run one round and stop.")
        parser.add_argument("--interval", type=int, default=60)

    def handle(self, *args, **opts):
        log.info("Worker started. Checking every %s seconds.", opts["interval"])
        worker = Worker()
        while True:
            worker.safe_round()
            if opts["once"]:
                break
            time.sleep(opts["interval"])
