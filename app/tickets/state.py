"""
Small JSON files in the status folder that every DiagTrack process shares:

  maintenance.json       the whole site shows "Back shortly" (during a restore)
  readonly.json          nobody can change anything (while moving to a new server)
  jobs/<id>.json         backup, restore and check jobs: requested by the screens, run by the backup runner
  backup-state.json      per destination: last run, last success, last check, alerts sent
  backup-config.json     backup destinations, schedules and retention (edited on the Backups page)
  backup.json            one-line summary read by the health strip

Files live outside the database on purpose: a restore replaces the database, and the progress
of that restore must survive it.
"""
import json
import os
import uuid
from datetime import datetime, timezone as dt_timezone
from pathlib import Path

from django.conf import settings


def status_dir():
    path = Path(settings.STATUS_DIR)
    path.mkdir(parents=True, exist_ok=True)
    return path


def now_iso():
    return datetime.now(dt_timezone.utc).isoformat(timespec="seconds")


def parse_iso(value):
    if not value:
        return None
    try:
        when = datetime.fromisoformat(value)
    except ValueError:
        return None
    return when if when.tzinfo else when.replace(tzinfo=dt_timezone.utc)


def read_json(path, default=None):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def write_json(path, data):
    """Write atomically, so a reader never sees half a file."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(data, indent=1, default=str), encoding="utf-8")
    os.replace(tmp, path)


# --------------------------------------------------------------------------
# Maintenance and read-only
# --------------------------------------------------------------------------
def maintenance():
    return read_json(status_dir() / "maintenance.json")


def set_maintenance(reason, job=""):
    write_json(status_dir() / "maintenance.json", {"reason": reason, "since": now_iso(), "job": job})


def clear_maintenance():
    (status_dir() / "maintenance.json").unlink(missing_ok=True)


def readonly():
    return read_json(status_dir() / "readonly.json")


def set_readonly(reason, by=""):
    write_json(status_dir() / "readonly.json", {"reason": reason, "since": now_iso(), "by": by})


def clear_readonly():
    (status_dir() / "readonly.json").unlink(missing_ok=True)


# --------------------------------------------------------------------------
# Jobs
# --------------------------------------------------------------------------
QUEUED, RUNNING, DONE, FAILED = "queued", "running", "done", "failed"


def jobs_dir():
    path = status_dir() / "jobs"
    path.mkdir(parents=True, exist_ok=True)
    return path


def new_job(kind, params=None, by=""):
    job = {"id": uuid.uuid4().hex[:12], "kind": kind, "params": params or {}, "by": by, "status": QUEUED,
           "percent": 0, "step": "Waiting to start", "message": "", "result": {}, "created": now_iso(),
           "started": None, "finished": None, "log": []}
    write_json(jobs_dir() / f"{job['id']}.json", job)
    return job


def get_job(job_id):
    if not job_id or not job_id.isalnum():
        return None
    return read_json(jobs_dir() / f"{job_id}.json")


def save_job(job):
    write_json(jobs_dir() / f"{job['id']}.json", job)


def list_jobs(limit=20):
    items = []
    for path in jobs_dir().glob("*.json"):
        job = read_json(path)
        if job:
            items.append(job)
    items.sort(key=lambda j: j.get("created", ""), reverse=True)
    for old in items[200:]:  # keep the folder small
        (jobs_dir() / f"{old['id']}.json").unlink(missing_ok=True)
    return items[:limit]


def queued_jobs():
    return sorted((j for j in list_jobs(200) if j.get("status") == QUEUED), key=lambda j: j["created"])


def active_job():
    for job in list_jobs(50):
        if job.get("status") in (QUEUED, RUNNING):
            return job
    return None


# --------------------------------------------------------------------------
# A simple lock, so the Task Scheduler safety net and the runner never back up at the same time
# --------------------------------------------------------------------------
LOCK_STALE_SECONDS = 6 * 3600


def take_lock(name):
    import time
    path = status_dir() / f"{name}.lock"
    try:
        if path.exists() and time.time() - path.stat().st_mtime > LOCK_STALE_SECONDS:
            path.unlink(missing_ok=True)
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        return False
    with os.fdopen(fd, "w") as f:
        f.write(f"{os.getpid()} {now_iso()}")
    return True


def release_lock(name):
    (status_dir() / f"{name}.lock").unlink(missing_ok=True)
