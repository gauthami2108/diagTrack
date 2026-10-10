"""
Backup destinations, their schedules and their retention.

The settings live in the status folder (backup-config.json), not in the database, so restoring an
older backup never changes where backups go. The Backups page edits them.
"""
import os
import sys
import uuid
from datetime import datetime, timedelta
from pathlib import Path

from django.utils import timezone

from .. import crypto, state

FREQUENCIES = [("hours", "Every few hours"), ("daily", "Daily"), ("weekly", "Weekly"), ("monthly", "Monthly")]
WEEKDAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
MARKER = ".diagtrack-backup-target"


def default_destination(path=None):
    return {
        "id": uuid.uuid4().hex[:8], "name": "Main backup folder",
        "path": path or os.environ.get("BACKUP_DIR", "/backups"),
        "enabled": True, "frequency": "daily", "every_hours": 6, "time": "01:30", "weekday": 6, "monthday": 1,
        "keep_daily": 14, "keep_weekly": 8, "keep_monthly": 12, "keep_manual_days": 30, "alert_after_hours": 26,
        "require_marker": False, "nas_user": "", "nas_password": "",
    }


def config_path():
    return state.status_dir() / "backup-config.json"


def load_config():
    cfg = state.read_json(config_path())
    if not cfg:
        cfg = {"destinations": [default_destination()], "alert_users": [], "verify_days": 7, "test_restore_days": 30,
               "created": state.now_iso()}
        save_config(cfg)
    cfg.setdefault("alert_users", [])
    cfg.setdefault("verify_days", 7)
    cfg.setdefault("test_restore_days", 30)
    for d in cfg["destinations"]:
        for k, v in default_destination().items():
            d.setdefault(k, v)
    return cfg


def save_config(cfg):
    state.write_json(config_path(), cfg)


def find(cfg, dest_id):
    return next((d for d in cfg["destinations"] if d["id"] == dest_id), None)


def nas_password(dest):
    return crypto.decrypt(dest.get("nas_password", ""))


# --------------------------------------------------------------------------
# Schedule
# --------------------------------------------------------------------------
def _at(day, hhmm):
    try:
        h, m = (int(x) for x in hhmm.split(":"))
    except (ValueError, AttributeError):
        h, m = 1, 30
    return timezone.make_aware(datetime(day.year, day.month, day.day, h % 24, m % 60))


def last_slot(dest, now=None):
    """The most recent time a backup was due, in local time."""
    now = timezone.localtime(now or timezone.now())
    today = now.date()
    freq = dest.get("frequency", "daily")
    if freq == "hours":
        step = max(1, min(24, int(dest.get("every_hours") or 6)))
        anchor = _at(today, dest.get("time", "00:00"))
        if anchor > now:
            anchor -= timedelta(days=1)
        slots = int((now - anchor).total_seconds() // (step * 3600))
        return anchor + timedelta(hours=step * slots)
    slot = _at(today, dest.get("time", "01:30"))
    if freq == "daily":
        return slot if slot <= now else slot - timedelta(days=1)
    if freq == "weekly":
        back = (today.weekday() - int(dest.get("weekday", 6))) % 7
        slot = _at(today - timedelta(days=back), dest.get("time", "01:30"))
        return slot if slot <= now else slot - timedelta(days=7)
    # monthly
    day = max(1, min(28, int(dest.get("monthday", 1))))
    slot = _at(today.replace(day=day), dest.get("time", "01:30"))
    if slot > now:
        prev = (today.replace(day=1) - timedelta(days=1)).replace(day=day)
        slot = _at(prev, dest.get("time", "01:30"))
    return slot


def next_slot(dest, now=None):
    now = timezone.localtime(now or timezone.now())
    probe = now
    last = last_slot(dest, now)
    for hours in range(1, 24 * 32):
        probe = now + timedelta(hours=hours)
        nxt = last_slot(dest, probe)
        if nxt > last:
            return nxt
    return None


def describe(dest):
    freq = dest.get("frequency", "daily")
    t = dest.get("time", "01:30")
    if freq == "hours":
        return f"Every {dest.get('every_hours', 6)} hours from {t}"
    if freq == "daily":
        return f"Daily at {t}"
    if freq == "weekly":
        return f"Weekly on {WEEKDAYS[int(dest.get('weekday', 6))]} at {t}"
    return f"Monthly on day {dest.get('monthday', 1)} at {t}"


# --------------------------------------------------------------------------
# Retention: keep the newest backup of each of the last N days, N weeks and N months
# --------------------------------------------------------------------------
def to_keep(entries, dest):
    """entries: dicts with 'name' and 'created' (aware datetime), any order. Returns the names to keep."""
    entries = sorted(entries, key=lambda e: e["created"], reverse=True)
    keep = set()
    if entries:
        keep.add(entries[0]["name"])  # never remove the newest
    rules = (("keep_daily", lambda d: d.date()), ("keep_weekly", lambda d: d.isocalendar()[:2]),
             ("keep_monthly", lambda d: (d.year, d.month)))
    for field, bucket in rules:
        limit = max(0, int(dest.get(field) or 0))
        seen = []
        for e in entries:
            b = bucket(timezone.localtime(e["created"]))
            if b in seen:
                continue
            if len(seen) >= limit:
                break
            seen.append(b)
            keep.add(e["name"])
    return keep


# --------------------------------------------------------------------------
# Reaching the folder
# --------------------------------------------------------------------------
class DestinationError(Exception):
    pass


def _connect_unc(path, user, password):
    """Log on to a Windows network share for this process, without putting the password on a command line."""
    import ctypes
    from ctypes import wintypes

    class NetResource(ctypes.Structure):
        _fields_ = [("dwScope", wintypes.DWORD), ("dwType", wintypes.DWORD), ("dwDisplayType", wintypes.DWORD),
                    ("dwUsage", wintypes.DWORD), ("lpLocalName", wintypes.LPWSTR),
                    ("lpRemoteName", wintypes.LPWSTR), ("lpComment", wintypes.LPWSTR),
                    ("lpProvider", wintypes.LPWSTR)]

    parts = path.lstrip("\\").split("\\")
    share = "\\\\" + "\\".join(parts[:2])
    res = NetResource()
    res.dwType = 1  # RESOURCETYPE_DISK
    res.lpRemoteName = share
    mpr = ctypes.windll.mpr
    mpr.WNetAddConnection2W.argtypes = [ctypes.POINTER(NetResource), wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD]
    code = mpr.WNetAddConnection2W(ctypes.byref(res), password, user, 0)
    if code not in (0, 1219, 85):  # ok, already connected with other credentials, already assigned
        raise DestinationError(f"Could not log on to {share} (Windows error {code}). Check the NAS user name "
                               "and password on the Backups page.")


def prepare(dest):
    """Make sure the folder is reachable and writable. Returns its Path."""
    path = dest.get("path", "").strip()
    if not path:
        raise DestinationError("No folder is set for this destination.")
    if sys.platform == "win32" and path.startswith("\\\\") and dest.get("nas_user"):
        _connect_unc(path, dest["nas_user"], nas_password(dest))
    root = Path(path)
    if dest.get("require_marker") and not (root / MARKER).exists():
        raise DestinationError(f"The NAS is not connected: the marker file {MARKER} is missing in {path}.")
    try:
        (root / "backups").mkdir(parents=True, exist_ok=True)
        (root / "attachments").mkdir(parents=True, exist_ok=True)
        probe = root / f".write-test-{uuid.uuid4().hex[:6]}"
        probe.write_text("ok")
        ok = probe.read_text() == "ok"
        probe.unlink()
    except OSError as exc:
        raise DestinationError(f"Cannot write to {path}: {exc.strerror or exc}.") from exc
    if not ok:
        raise DestinationError(f"{path} did not read back what was written.")
    return root


def free_space(root):
    try:
        import shutil
        return shutil.disk_usage(root).free
    except OSError:
        return None
