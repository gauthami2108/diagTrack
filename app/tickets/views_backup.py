"""
The Backups page (administrators): destinations, schedules, retention, Back up now, full copies,
checks, test restores, restores and moving to another server.

The screens never run a backup themselves. They queue a job; the backup runner (inside the DiagTrack
service on Windows, the backup container in Docker) runs it and reports progress, which the page polls.
"""
import uuid
from datetime import timedelta

from django import forms
from django.contrib import messages
from django.http import Http404, JsonResponse
from django.shortcuts import redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from . import crypto, state
from .backup import destinations as dests
from .backup.runner import human_size, load_state
from .models import User
from .views import admin_only

HHMM = forms.RegexField(regex=r"^([01]\d|2[0-3]):[0-5]\d$", initial="01:30",
                        error_messages={"invalid": "Use a 24-hour time such as 01:30."})


class DestinationForm(forms.Form):
    name = forms.CharField(max_length=60, initial="Backup folder")
    path = forms.CharField(max_length=300, label="Folder", help_text="A folder this server can reach, for example "
                                                     "D:\\DiagTrack-Backups or \\\\nas01\\backups\\diagtrack. "
                                                     "In Docker: /backups or /backups2.")
    enabled = forms.BooleanField(required=False, initial=True, label="Switched on")
    frequency = forms.ChoiceField(choices=dests.FREQUENCIES, initial="daily")
    every_hours = forms.IntegerField(min_value=1, max_value=24, initial=6, label="Every how many hours")
    time = HHMM
    weekday = forms.TypedChoiceField(choices=list(enumerate(dests.WEEKDAYS)), coerce=int, initial=6, label="Day of the week")
    monthday = forms.IntegerField(min_value=1, max_value=28, initial=1, label="Day of the month")
    keep_daily = forms.IntegerField(min_value=0, max_value=3650, initial=14, label="Daily copies to keep")
    keep_weekly = forms.IntegerField(min_value=0, max_value=520, initial=8, label="Weekly copies to keep")
    keep_monthly = forms.IntegerField(min_value=0, max_value=240, initial=12, label="Monthly copies to keep")
    keep_manual_days = forms.IntegerField(min_value=0, max_value=3650, initial=30,
                                          label="Keep manual backups for (days)")
    alert_after_hours = forms.IntegerField(min_value=1, max_value=24 * 40, initial=26, label="Alert if no backup for (hours)")
    require_marker = forms.BooleanField(required=False, label="Treat a missing marker file as 'NAS not connected'",
                                        help_text=f"Put an empty file named {dests.MARKER} in the NAS folder first.")
    nas_user = forms.CharField(max_length=120, required=False, label="NAS user name (Windows network shares only)")
    nas_password = forms.CharField(max_length=200, required=False, label="NAS password",
                                   widget=forms.PasswordInput(attrs={"autocomplete": "new-password",
                                                                     "placeholder": "Saved. Type a new one to change it"}))

    def clean(self):
        data = super().clean()
        if data.get("keep_daily") == data.get("keep_weekly") == data.get("keep_monthly") == 0:
            raise forms.ValidationError("Keep at least one daily, weekly or monthly copy.")
        return data


def _catalog():
    return state.read_json(state.status_dir() / "backup-catalog.json", {}) or {}


def _when(iso):
    return state.parse_iso(iso)


def _dest_view(d, st, cat):
    ds = st["destinations"].get(d["id"], {})
    c = cat.get("destinations", {}).get(d["id"], {})
    items = []
    for it in c.get("items", []):
        items.append(dict(it, created_at=_when(it["created"]), size_text=human_size(it["size"])))
    last_ok = _when(ds.get("last_ok"))
    limit = timedelta(hours=int(d.get("alert_after_hours") or 26))
    if not d.get("enabled"):
        status = "off"
    elif ds.get("last_status") == "failed":
        status = "bad"
    elif last_ok and timezone.now() - last_ok <= limit:
        status = "ok"
    elif last_ok:
        status = "late"
    else:
        status = "never"
    return {"d": d, "s": ds, "status": status, "last_ok": last_ok, "items": items, "folder_ok": c.get("ok", True),
            "folder_error": c.get("error", ""), "free": human_size(c["free"]) if c.get("free") else "",
            "schedule": dests.describe(d), "next": dests.next_slot(d) if d.get("enabled") else None,
            "last_verify": _when(ds.get("last_verify")), "verify_ok": ds.get("last_verify_ok", True)}


@admin_only
def backups_view(request):
    cfg = dests.load_config()
    st = load_state()
    cat = _catalog()
    rows = [_dest_view(d, st, cat) for d in cfg["destinations"]]
    for r in rows:
        initial = {k: v for k, v in r["d"].items() if k != "nas_password"}
        r["form"] = DestinationForm(initial=initial, auto_id=f"d{r['d']['id']}_%s")
    enabled = [r for r in rows if r["d"].get("enabled")]
    last_good = max((r["last_ok"] for r in rows if r["last_ok"]), default=None)
    nexts = [r["next"] for r in enabled if r["next"]]
    last_test = _when(st.get("last_test_restore"))
    jobs = state.list_jobs(8)
    for j in jobs:
        j["finished_at"] = _when(j.get("finished"))
    ro = state.readonly()
    active = next((j for j in jobs if j["status"] in (state.QUEUED, state.RUNNING)), None)
    return render(request, "backups.html", {
        "rows": rows, "bcfg": cfg, "last_good": last_good, "next_backup": min(nexts) if nexts else None,
        "last_test": last_test, "test_ok": st.get("last_test_ok", True), "test_message": st.get("last_test_message", ""),
        "test_stale": not last_test or timezone.now() - last_test > timedelta(days=40),
        "jobs": jobs, "active": active, "form": DestinationForm(auto_id="new_%s"), "readonly": ro,
        "readonly_since": _when(ro.get("since")) if ro else None,
        "retired": state.read_json(state.status_dir() / "retired.json"),
        "admins": User.objects.filter(is_active=True).order_by("first_name", "username"),
        "alert_users": set(cfg.get("alert_users", [])), "catalog_updated": _when(cat.get("updated")),
        "runner_seen": _when(cat.get("updated")) and timezone.now() - _when(cat.get("updated")) < timedelta(minutes=3),
    })


def _queue(request, kind, params, label):
    busy = state.active_job()
    if busy:
        messages.error(request, "Another backup task is still running. Wait for it to finish, then try again.")
        return None
    job = state.new_job(kind, params, by=request.user.username)
    messages.success(request, f"{label} started. Progress is shown below.")
    return job


@admin_only
@require_POST
def backups_action(request):
    p = request.POST
    action = p.get("do", "")
    cfg = dests.load_config()
    dest_id = p.get("dest", "")
    dest = dests.find(cfg, dest_id) if dest_id else None
    if dest_id and not dest:
        raise Http404
    name = p.get("name", "")

    if action == "backup":
        _queue(request, "backup", {"dest": dest_id}, "Backup")
    elif action == "full":
        if not p.get("to", "").strip():
            messages.error(request, "Type the folder for the full copy.")
        else:
            _queue(request, "full", {"to": p["to"].strip()}, "Full copy")
    elif action == "verify":
        _queue(request, "verify", {"dest": dest_id, "name": name}, "Backup check")
    elif action == "test_restore":
        _queue(request, "test_restore", {"dest": dest_id, "name": name}, "Test restore")
    elif action == "test_destination":
        _queue(request, "test_destination", {"dest": dest_id}, "Folder check")
    elif action == "restore":
        if p.get("confirm", "").strip().upper() != "RESTORE":
            messages.error(request, "Type RESTORE in capitals to confirm. Nothing was changed.")
        else:
            params = {"dest": dest_id, "name": name} if dest_id else {"path": p.get("path", "").strip()}
            _queue(request, "restore", params, "Restore")
    elif action == "save_dest":
        form = DestinationForm(p)
        if not form.is_valid():
            for field, errs in form.errors.items():
                label = "Backup folder" if field == "__all__" else (form.fields[field].label or field.replace("_", " ").capitalize())
                messages.error(request, f"{label}: {errs[0]}")
            return redirect("backups")
        data = form.cleaned_data
        target = dest or dict(dests.default_destination(), id=uuid.uuid4().hex[:8])
        old_pw = target.get("nas_password", "")
        target.update({k: v for k, v in data.items() if k != "nas_password"})
        target["nas_password"] = crypto.encrypt(data["nas_password"]) if data["nas_password"] else old_pw
        if not dest:
            cfg["destinations"].append(target)
        dests.save_config(cfg)
        messages.success(request, f"{target['name']} saved. Use 'Check folder' to test it.")
    elif action == "remove_dest":
        if len(cfg["destinations"]) <= 1:
            messages.error(request, "Keep at least one backup destination.")
        else:
            cfg["destinations"] = [d for d in cfg["destinations"] if d["id"] != dest_id]
            dests.save_config(cfg)
            messages.success(request, "Destination removed. The backups already in that folder were not deleted.")
    elif action == "alerts":
        ids = [int(x) for x in p.getlist("alert_users") if x.isdigit()]
        cfg["alert_users"] = ids
        dests.save_config(cfg)
        messages.success(request, "Backup alert recipients saved.")
    elif action == "moveout":
        if p.get("confirm", "").strip().upper() != "MOVE":
            messages.error(request, "Type MOVE in capitals to confirm. Nothing was changed.")
        elif not p.get("to", "").strip():
            messages.error(request, "Type the folder for the move file.")
        else:
            _queue(request, "moveout", {"to": p["to"].strip(), "by": request.user.username}, "Preparing to move")
    elif action == "endmove":
        state.clear_readonly()
        messages.success(request, "The move was cancelled. DiagTrack accepts changes again.")
    elif action == "retire":
        if p.get("confirm", "").strip().upper() != "RETIRE":
            messages.error(request, "Type RETIRE in capitals to confirm. Nothing was changed.")
        else:
            state.write_json(state.status_dir() / "retired.json", {"since": state.now_iso(), "by": request.user.username})
            state.set_readonly("This server has been retired. DiagTrack now runs on the new server.", by=request.user.username)
            messages.success(request, "This server is retired: it stays read-only and runs no more scheduled backups. "
                                      "Its data is kept. Stop the DiagTrack service when the new server has run well for 30 days.")
    else:
        messages.error(request, "Unknown action.")
    return redirect("backups")


@admin_only
def backup_job(request, job_id):
    job = state.get_job(job_id)
    if not job:
        raise Http404
    return JsonResponse({k: job.get(k) for k in ("id", "kind", "status", "percent", "step", "message", "created",
                                                   "started", "finished")})
