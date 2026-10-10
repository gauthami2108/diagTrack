"""
The backup runner. Runs inside the DiagTrack service (native Windows) or the backup container (Docker).

Every few seconds it picks up jobs the screens asked for (Back up now, Save a full copy, Restore,
Check, Test restore). Every minute it runs scheduled backups that are due (including ones missed
while the computer was off), the weekly read-back check, the monthly test restore, and sends alerts
when a backup fails or is overdue.
"""
import logging
import time
import traceback
from datetime import timedelta
from pathlib import Path

from django.conf import settings
from django.db import close_old_connections
from django.utils import timezone

from .. import state
from . import destinations as dests
from . import engine

log = logging.getLogger("diagtrack.backup")
RETRY_AFTER = timedelta(minutes=30)
START_DELAY = 90  # seconds after start before scheduled work, so the screens come up first


def load_state():
    st = state.read_json(state.status_dir() / "backup-state.json", {}) or {}
    st.setdefault("destinations", {})
    return st


def save_state(st):
    state.write_json(state.status_dir() / "backup-state.json", st)


def dest_state(st, dest_id):
    return st["destinations"].setdefault(dest_id, {})


def alert(kind, title, body, cfg=None):
    from ..models import User
    from ..notify import notify
    cfg = cfg or dests.load_config()
    users = list(User.objects.filter(role=User.ADMIN, is_active=True))
    users += list(User.objects.filter(pk__in=cfg.get("alert_users", []), is_active=True))
    notify(users, None, title, body, urgent=True, kind=kind, link="/settings/backups/")


def write_summary(cfg=None, st=None):
    """backup.json: the one-line state the health strip shows."""
    cfg = cfg or dests.load_config()
    st = st or load_state()
    now = timezone.now()
    enabled = [d for d in cfg["destinations"] if d.get("enabled")]
    if not enabled:
        state.write_json(state.status_dir() / "backup.json",
                         {"ok": False, "finished": state.now_iso(), "message": "No backup destination is switched on."})
        return
    problems, newest, size = [], None, ""
    for d in enabled:
        ds = dest_state(st, d["id"])
        last_ok = state.parse_iso(ds.get("last_ok"))
        if ds.get("last_status") == "failed":
            problems.append(f"{d['name']}: {ds.get('last_message', 'failed')}")
        elif not last_ok or now - last_ok > timedelta(hours=int(d.get("alert_after_hours") or 26)):
            problems.append(f"{d['name']}: no backup in the last {d.get('alert_after_hours', 26)} hours")
        if last_ok and (newest is None or last_ok > newest):
            newest, size = last_ok, ds.get("last_size_text", "")
    message = "; ".join(problems) if problems else f"Backups up to date on {len(enabled)} destination(s)."
    state.write_json(state.status_dir() / "backup.json", {
        "ok": not problems, "finished": (newest or now).isoformat(timespec="seconds"), "message": message,
        "size": size, "last_test_restore": st.get("last_test_restore"), "last_test_ok": st.get("last_test_ok")})


def refresh_catalog(cfg=None):
    """backup-catalog.json: what is in each backup folder, for the Backups page. Only the readable summaries."""
    cfg = cfg or dests.load_config()
    out = {"updated": state.now_iso(), "destinations": {}}
    for d in cfg["destinations"]:
        entry = {"ok": True, "error": "", "items": [], "free": None}
        try:
            root = dests.prepare(d)
            entry["free"] = dests.free_space(root)
            for e in engine.catalog(root)[:200]:
                s = e["summary"]
                entry["items"].append({"name": e["name"], "created": e["created"].isoformat(), "size": e["size"],
                                       "kind": s.get("kind", ""), "version": s.get("app_version", ""),
                                       "tickets": (s.get("counts") or {}).get("tickets"),
                                       "attachments": (s.get("attachments") or {}).get("count"),
                                       "platform": s.get("platform", "")})
        except Exception as exc:
            entry.update(ok=False, error=str(exc)[:300])
        out["destinations"][d["id"]] = entry
    state.write_json(state.status_dir() / "backup-catalog.json", out)
    return out


def human_size(n):
    for unit in ("bytes", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return f"{n:.0f} {unit}" if unit == "bytes" else f"{n:.1f} {unit}"
        n /= 1024


class Runner:
    def __init__(self):
        self.started = time.monotonic()
        self.last_minute = 0.0

    # ----------------------------------------------------------------- jobs
    def run_job(self, job):
        if not state.take_lock("backup"):
            return job  # another backup process is busy; the job stays queued and runs next time
        job.update(status=state.RUNNING, started=state.now_iso(), step="Starting", percent=1)
        state.save_job(job)

        def progress(percent, step):
            if percent is not None:
                job["percent"] = int(percent)
            job["step"] = step
            state.save_job(job)

        try:
            handler = getattr(self, f"job_{job['kind']}")
            job["result"] = handler(job.get("params", {}), progress) or {}
            job.update(status=state.DONE, percent=100, step=job["result"].get("step", "Done"),
                       message=job["result"].get("message", ""))
        except Exception as exc:  # report every failure in plain words, keep the detail in the log
            log.warning("Job %s (%s) failed: %s", job["id"], job["kind"], exc)
            job.update(status=state.FAILED, message=str(exc) or exc.__class__.__name__,
                       detail=traceback.format_exc()[-2000:])
        finally:
            job["finished"] = state.now_iso()
            state.save_job(job)
            state.release_lock("backup")
            close_old_connections()
        return job

    def _dest(self, params):
        cfg = dests.load_config()
        dest = dests.find(cfg, params.get("dest")) if params.get("dest") else None
        return cfg, dest

    def job_backup(self, params, progress):
        cfg, dest = self._dest(params)
        targets = [dest] if dest else [d for d in cfg["destinations"] if d.get("enabled")]
        if not targets:
            raise engine.BackupError("No backup destination is switched on. Add one on the Backups page.")
        results = []
        for i, d in enumerate(targets):
            def sub(p, s, i=i):
                progress(None if p is None else (100 * i + p) / len(targets), f"{d['name']}: {s}")
            results.append(self.backup_to(d, "manual", sub, raise_errors=len(targets) == 1))
        failed = [r for r in results if not r.get("ok")]
        if failed:
            raise engine.BackupError("; ".join(r["message"] for r in failed))
        r = results[-1]
        return {"message": f"Backup saved: {r['name']} ({r['size_text']}) in {r['folder']}.", "files": results,
                "step": "Backup completed"}

    def job_full(self, params, progress):
        folder = (params.get("to") or "").strip()
        if not folder:
            raise engine.BackupError("Choose the folder for the full copy.")
        out = engine.create(kind="full", full_to=folder, progress=progress)
        return {"message": f"Full copy saved: {out['file']} ({human_size(out['size'])}). "
                           f"Checksum SHA-256 {out['sha256'][:16]}...", "file": out["file"], "sha256": out["sha256"],
                "step": "Full copy completed"}

    def job_verify(self, params, progress):
        cfg, dest = self._dest(params)
        root = dests.prepare(dest) if dest else None
        path = self._pick(root, params)
        progress(20, f"Reading {Path(path).name}")
        res = engine.verify(path, root, sample=int(params.get("sample", 20)))
        st = load_state()
        if dest:
            ds = dest_state(st, dest["id"])
            ds.update(last_verify=state.now_iso(), last_verify_ok=True, last_verify_message="")
            save_state(st)
        return {"message": f"{Path(path).name} is intact: every part matched its checksum and "
                           f"{res['attachments']} attachment(s) are present.", "step": "Check passed"}

    def job_test_restore(self, params, progress):
        cfg, dest = self._dest(params)
        root = dests.prepare(dest) if dest else None
        path = self._pick(root, params)
        res = engine.test_restore(path, root, progress)
        st = load_state()
        st.update(last_test_restore=state.now_iso(), last_test_ok=True, last_test_message="", last_test_file=res["backup"])
        save_state(st)
        write_summary()
        c = res["counts"]
        return {"message": f"Test restore of {res['backup']} passed: {c.get('tickets', 0)} tickets, "
                           f"{c.get('users', 0)} users, {c.get('attachments', 0)} attachments matched.",
                "step": "Test restore passed"}

    def job_restore(self, params, progress):
        cfg, dest = self._dest(params)
        root = dests.prepare(dest) if dest else None
        path = self._pick(root, params)
        state.set_maintenance("A backup is being restored.", job=params.get("_job", ""))
        try:
            res = engine.restore(path, root, progress)
        finally:
            state.clear_maintenance()
        renamed = len(res["renamed"])
        return {"message": f"Restored {res['backup']} (made {res['created']}). {res['counts'].get('tickets', 0)} tickets, "
                           f"{res['attachments_restored']} attachment(s) copied back"
                           f"{f', {renamed} renamed to suit this computer' if renamed else ''}. The database as it was "
                           f"before is kept as {res['previous_database']} until the next restore.",
                "step": "Restore completed", "detail": res}

    def job_test_destination(self, params, progress):
        cfg, dest = self._dest(params)
        if not dest:
            raise engine.BackupError("That destination no longer exists.")
        root = dests.prepare(dest)
        engine.store_salt(root)
        free = dests.free_space(root)
        return {"message": f"{dest['name']}: the folder can be written and read"
                           f"{f', {human_size(free)} free' if free is not None else ''}.", "step": "Folder works"}

    def job_moveout(self, params, progress):
        state.set_readonly("Moving to a new server.", by=params.get("by", ""))
        res = self.job_full(params, progress)
        res["message"] = ("DiagTrack is now read-only. " + res["message"] +
                          " Copy this file to the new computer and choose Move in during setup.")
        return res

    def _pick(self, root, params):
        if params.get("path"):
            path = Path(params["path"])
        elif params.get("name"):
            if not root:
                raise engine.BackupError("Choose the backup folder first.")
            path = Path(root) / "backups" / Path(params["name"]).name
        else:
            entries = engine.catalog(root) if root else []
            if not entries:
                raise engine.BackupError("There is no backup in this folder yet.")
            path = Path(entries[0]["path"])
        if not path.exists():
            raise engine.BackupError(f"{path} was not found.")
        return str(path)

    # ------------------------------------------------------------ backups
    def backup_to(self, dest, kind, progress, raise_errors=False):
        st = load_state()
        ds = dest_state(st, dest["id"])
        ds["last_attempt"] = state.now_iso()
        save_state(st)
        try:
            root = dests.prepare(dest)
            out = engine.create(root, kind=kind, progress=progress)
            removed, dropped = engine.apply_retention(root, dest)
            st = load_state()
            ds = dest_state(st, dest["id"])
            was_failing = ds.get("last_status") == "failed"
            ds.update(last_ok=state.now_iso(), last_status="ok", last_message="", last_file=out["name"],
                      last_size_text=human_size(out["size"]), failure_alerted=False, overdue_alerted=False, removed=len(removed))
            save_state(st)
            write_summary(st=st)
            if was_failing:
                log.info("Backups to %s work again.", dest["name"])
            return {"ok": True, "name": out["name"], "size_text": human_size(out["size"]), "folder": dest["path"],
                    "removed": len(removed), "attachments_removed": dropped}
        except Exception as exc:
            st = load_state()
            ds = dest_state(st, dest["id"])
            ds.update(last_status="failed", last_message=str(exc)[:400])
            first_failure = not ds.get("failure_alerted")
            ds["failure_alerted"] = True
            save_state(st)
            write_summary(st=st)
            log.warning("Backup to %s failed: %s", dest["name"], exc)
            if first_failure:
                alert("backupfail", f"Backup to {dest['name']} failed", str(exc)[:300])
            if raise_errors:
                raise
            return {"ok": False, "message": f"{dest['name']}: {exc}"}

    def scheduled(self):
        cfg = dests.load_config()
        now = timezone.now()
        for dest in cfg["destinations"]:
            if not dest.get("enabled"):
                continue
            ds = dest_state(load_state(), dest["id"])
            slot = dests.last_slot(dest, now)
            last_ok = state.parse_iso(ds.get("last_ok"))
            last_try = state.parse_iso(ds.get("last_attempt"))
            due = (last_ok is None or last_ok < slot) and (last_try is None or last_try < slot or now - last_try >= RETRY_AFTER)
            if due and state.take_lock("backup"):
                log.info("Scheduled backup to %s.", dest["name"])
                try:
                    self.backup_to(dest, "scheduled", lambda p, s: None)
                finally:
                    state.release_lock("backup")
            # overdue alert, once per episode
            st = load_state()
            ds = dest_state(st, dest["id"])
            last_ok = state.parse_iso(ds.get("last_ok"))
            limit = timedelta(hours=int(dest.get("alert_after_hours") or 26))
            since = last_ok or state.parse_iso(cfg.get("created")) or None
            if since and now - since > limit and not ds.get("overdue_alerted"):
                ds["overdue_alerted"] = True
                save_state(st)
                alert("backuplate", f"No backup to {dest['name']} for {int((now - since).total_seconds() // 3600)} hours",
                      ds.get("last_message") or "Check the Backups page.", cfg)
            # weekly read-back check of the newest backup
            last_verify = state.parse_iso(ds.get("last_verify"))
            if last_ok and (last_verify is None or now - last_verify > timedelta(days=int(cfg.get("verify_days", 7)))):
                job = state.new_job("verify", {"dest": dest["id"], "sample": 50}, by="schedule")
                done = self.run_job(job)
                if done["status"] == state.FAILED:
                    st = load_state()
                    ds = dest_state(st, dest["id"])
                    ds.update(last_verify=state.now_iso(), last_verify_ok=False, last_verify_message=done["message"])
                    save_state(st)
                    alert("backupfail", f"Backup check failed on {dest['name']}", done["message"][:300], cfg)
        # monthly test restore from the first working destination
        st = load_state()
        last_test = state.parse_iso(st.get("last_test_restore"))
        if last_test is None or now - last_test > timedelta(days=int(cfg.get("test_restore_days", 30))):
            first = next((d for d in cfg["destinations"]
                          if d.get("enabled") and dest_state(st, d["id"]).get("last_status") == "ok"), None)
            if first:
                job = state.new_job("test_restore", {"dest": first["id"]}, by="schedule")
                done = self.run_job(job)
                if done["status"] == state.FAILED:
                    st = load_state()
                    st.update(last_test_restore=state.now_iso(), last_test_ok=False, last_test_message=done["message"])
                    save_state(st)
                    write_summary(st=st)
                    alert("backupfail", "The monthly test restore failed", done["message"][:300], cfg)

    # --------------------------------------------------------------- loop
    def tick(self):
        close_old_connections()
        ran = False
        for job in state.queued_jobs():
            job.setdefault("params", {})["_job"] = job["id"]
            self.run_job(job)
            ran = True
        if ran or not getattr(self, "catalog_done", False):
            refresh_catalog()
            self.catalog_done = True
        if time.monotonic() - self.started < START_DELAY or time.monotonic() - self.last_minute < 60:
            return
        self.last_minute = time.monotonic()
        if state.maintenance():
            return
        retired = state.read_json(state.status_dir() / "retired.json")
        if not retired:
            self.scheduled()
        write_summary()
        refresh_catalog()

    def safe_tick(self):
        try:
            self.tick()
        except Exception:
            log.exception("Backup runner round failed; trying again shortly.")
        finally:
            close_old_connections()
