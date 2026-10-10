"""
Background worker. Runs forever inside the 'worker' container.
Every minute it: checks SLA deadlines, fires escalations, closes old resolved
tickets and sends waiting email / SMS / WhatsApp alerts.
"""
import logging
import time

from django.core.management.base import BaseCommand
from django.db import close_old_connections
from django.utils import timezone

from tickets import notify, services

log = logging.getLogger("diagtrack.worker")


class Command(BaseCommand):
    help = "Run the DiagTrack background worker."

    def add_arguments(self, parser):
        parser.add_argument("--once", action="store_true", help="Run one round and stop.")
        parser.add_argument("--interval", type=int, default=60)

    def handle(self, *args, **opts):
        log.info("Worker started. Checking every %s seconds.", opts["interval"])
        amc_day = None
        while True:
            try:
                close_old_connections()
                fired = services.run_sla_checks()
                closed = services.auto_close()
                if amc_day != timezone.localdate():  # once a day, and at start
                    amc = services.amc_checks()
                    amc_day = timezone.localdate()
                    if amc:
                        log.info("Sent %s AMC expiry alerts.", amc)
                sent = notify.send_pending()
                services.beat("worker", f"{fired} escalations, {closed} auto-closed, {sent} alerts sent")
                if fired or closed or sent:
                    log.info("Round done: %s escalations, %s auto-closed, %s alerts sent.", fired, closed, sent)
            except Exception:  # never let one bad round stop the worker
                log.exception("Worker round failed; trying again next minute.")
            if opts["once"]:
                break
            time.sleep(opts["interval"])
