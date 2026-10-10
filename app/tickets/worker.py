"""
One round of background work: SLA checks, escalations, auto-close, AMC warnings and sending alerts.
Run every minute by `manage.py runworker` (Docker) or by `manage.py serve` (native Windows).
"""
import logging

from django.db import close_old_connections
from django.utils import timezone

from . import notify, services, state

log = logging.getLogger("diagtrack.worker")


class Worker:
    def __init__(self):
        self.amc_day = None

    def round(self):
        close_old_connections()
        if state.maintenance():
            return "paused for maintenance"
        if state.readonly():
            # Moving to another server: no ticket may change, but waiting alerts still go out.
            sent = notify.send_pending()
            services.beat("worker", f"read-only; {sent} alerts sent")
            return "read-only"
        fired = services.run_sla_checks()
        closed = services.auto_close()
        if self.amc_day != timezone.localdate():  # once a day, and at start
            amc = services.amc_checks()
            self.amc_day = timezone.localdate()
            if amc:
                log.info("Sent %s AMC expiry alerts.", amc)
        sent = notify.send_pending()
        services.beat("worker", f"{fired} escalations, {closed} auto-closed, {sent} alerts sent")
        if fired or closed or sent:
            log.info("Round done: %s escalations, %s auto-closed, %s alerts sent.", fired, closed, sent)
        return "ok"

    def safe_round(self):
        try:
            return self.round()
        except Exception:  # never let one bad round stop the worker
            log.exception("Worker round failed; trying again next minute.")
            return "error"
        finally:
            close_old_connections()
