"""
Docker setup. Runs inside the app image:  docker compose run --rm setup

Docker itself is installed by hand beforehand (Docker's own guide). This asks the questions, then writes:
  .env                        settings (no passwords)
  secrets/                    one file per secret, mounted read-only at /run/secrets in the containers
  data/status/backup-config.json   backup folders, schedule and retention
and, when moving in, a restore task the backup container runs as soon as it starts.

Afterwards:  docker compose up -d   then   docker compose run --rm setup verify
"""
import json
import os
import sys
import time
import urllib.request
import uuid
from pathlib import Path

from . import common, ui
from .ui import Failed

PROJECT = Path(os.environ.get("DIAGTRACK_PROJECT", "/project"))
SECRETS = PROJECT / "secrets"
STATUS = Path(os.environ.get("STATUS_DIR", "/data/status"))
IMPORT = PROJECT / "data" / "import"

# Settings the first Docker version kept in .env. Secrets move to ./secrets; the rest become backup defaults.
OLD_SECRET_KEYS = ("DB_PASSWORD", "SECRET_KEY", "ADMIN_PASSWORD")
OLD_BACKUP_KEYS = ("BACKUP_TIME", "BACKUP_KEEP_DAYS", "BACKUP_REQUIRE_MARKER")


def earlier_install():
    """True when this folder already holds a database made by the first Docker version (before ./secrets)."""
    folder = PROJECT / "data" / "postgres"
    try:
        return (folder / "PG_VERSION").exists() or any(folder.iterdir())
    except PermissionError:  # the database owns the folder: it is there, and not empty
        return True
    except OSError:
        return False


def old_secret_key(env):
    if env.get("SECRET_KEY"):
        return env["SECRET_KEY"]
    try:  # the first version created one here when .env had none
        return (STATUS / ".secret_key").read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def write_secret(name, value):
    SECRETS.mkdir(parents=True, exist_ok=True)
    os.chmod(SECRETS, 0o755)
    path = SECRETS / name
    path.write_text(value + "\n", encoding="utf-8")
    os.chmod(path, 0o644)  # the db and app containers run as different users and must read it


def read_secret(name):
    try:
        return (SECRETS / name).read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def read_env():
    values = {}
    try:
        for line in (PROJECT / ".env").read_text().splitlines():
            if "=" in line and not line.strip().startswith("#"):
                k, v = line.split("=", 1)
                values[k.strip()] = v.strip()
    except OSError:
        pass
    return values


def install():
    common.app_dir("/")
    steps = ui.Steps({"pre": "Checking the DiagTrack folder", "ask": "Questions and summary",
                      "write": "Writing settings and secrets", "next": "What to do next"})
    steps.start("pre")
    if not (PROJECT / "docker-compose.yml").exists():
        raise Failed("DT-D01", "The DiagTrack folder is not mounted.", todo="Run the command from the DiagTrack folder: "
                                                                            "docker compose run --rm setup")
    if read_secret("db_password"):
        ui.say("DiagTrack is already set up in this folder (the secrets folder exists).")
        ui.say("  To check it:            docker compose run --rm setup verify")
        ui.say("  To start it:            docker compose up -d")
        ui.say("  Never delete the secrets folder: the database password in it cannot be changed afterwards.")
        return 0
    steps.ok(str(PROJECT))

    steps.start("ask")
    old_env = read_env()
    if earlier_install():
        ui.say("An earlier version of DiagTrack is installed in this folder (its database is in data/postgres).")
        ui.say("Setup keeps every ticket, user and attachment, and moves its passwords out of .env into ./secrets.")
        mode = "upgrade"
    else:
        mode = ui.choose("What would you like to do?", [("new", "New install"),
                                                         ("move", "Move in from another DiagTrack (move file in data/import)")],
                         key="mode")
    a = {"mode": mode}
    if mode == "new":
        a["admin_user"] = ui.ask("DiagTrack administrator user name", "admin", key="admin_user")
        a["admin_password"] = ui.ask_secret("Administrator password (10 or more characters, letters and a digit)",
                                            check=ui.password_problem, key="admin_password")
    elif mode == "upgrade":
        ui.say("The administrator and staff logins stay as they are.")
    else:
        files = sorted(IMPORT.glob("*.dtbak")) if IMPORT.exists() else []
        if not files:
            raise Failed("DT-D02", "No move file was found in data/import.",
                         todo="Copy the .dtbak file from the old server into the folder data/import here, then run "
                              "setup again.")
        chosen = files[0] if len(files) == 1 else Path(ui.choose("Which file?", [(str(f), f.name) for f in files],
                                                                       key="move_file"))
        while True:
            pp = ui.ask_secret("Backup passphrase of the old server (from its recovery sheet)", confirm=False,
                               key="backup_passphrase")
            try:
                summary = common.read_move_file(chosen, pp)
                break
            except Failed as f:
                if ui.ANSWERS:
                    raise
                ui.say(ui.red(f"  {f.what}"))
        a.update(move_file=chosen, backup_passphrase=pp, move_summary=summary)
        ui.say(ui.green(f"  The file opens: {common.describe_summary(summary)}."))
    a["port"] = ui.ask("Web port staff will use on this computer", old_env.get("HTTP_PORT") or "80", key="port",
                       check=lambda v: None if v.isdigit() and 0 < int(v) < 65536 else "Type a port number.")
    a["site_url"] = ui.ask("Address staff will type in the browser", "http://diagtrack.local", key="site_url",
                           check=lambda v: None if v.startswith(("http://", "https://")) else "Start with http://")
    a["backup_dir"] = ui.ask("Backup folder on this computer (where the NAS is mounted)",
                             old_env.get("BACKUP_DIR") or "./data/backups",
                             key="backup1_path", hint="An absolute path such as /mnt/nas/diagtrack, or ./data/backups")
    a["backup_dir_2"] = ""
    if ui.yes("Add a second backup folder, for example another centre's NAS?", False, key="backup2_wanted"):
        a["backup_dir_2"] = ui.ask("Second backup folder on this computer", key="backup2_path",
                                   check=lambda v: None if v else "Type a folder.")
    a["backup_time"] = ui.ask("Daily backup time (24-hour clock)", old_env.get("BACKUP_TIME") or "01:30",
                              key="backup_time")
    if mode in ("new", "upgrade"):
        ui.say("Backups are always encrypted. Choose a passphrase: a short sentence is best.")
        a["backup_passphrase"] = ui.ask_secret("Backup passphrase (12 or more characters)",
                                               check=common.passphrase_problem, key="backup_passphrase")
    ui.section("Summary")
    for k, v in [("Install", {"move": "Move in", "upgrade": "Upgrade the earlier version"}.get(mode, "New install")), ("Web port", a["port"]),
                 ("Address", a["site_url"]), ("Backup folder", a["backup_dir"]),
                 ("Second backup folder", a["backup_dir_2"] or "none"), ("Daily backup at", a["backup_time"])]:
        ui.say(f"  {k + ':':<22}{v}")
    if not ui.yes("Write these settings?", True, key="confirm"):
        raise Failed("DT-Q09", "Cancelled. Nothing was written.")
    steps.ok()

    steps.start("write")
    field_key = common.new_key()
    if mode == "upgrade":
        # The existing database was created with this password (the first version's default was "diagtrack").
        # It is reachable only from inside Docker; keeping it is what lets the database start.
        write_secret("db_password", old_env.get("DB_PASSWORD") or "diagtrack")
        write_secret("secret_key", old_secret_key(old_env) or common.new_password(50))
    else:
        write_secret("db_password", common.new_password(32))
        write_secret("secret_key", common.new_password(50))
    write_secret("field_key", field_key)
    write_secret("backup_passphrase", a["backup_passphrase"])
    if mode == "new":
        write_secret("admin_password_hash", common.password_hash(a["admin_password"]))
    env = {k: v for k, v in old_env.items() if k not in OLD_SECRET_KEYS + OLD_BACKUP_KEYS}
    env.update({"TIME_ZONE": env.get("TIME_ZONE", "Asia/Kolkata"), "HTTP_PORT": a["port"], "SITE_URL": a["site_url"],
                "BACKUP_DIR": a["backup_dir"], "BACKUP_DIR_2": a["backup_dir_2"] or "./data/backups2",
                "ADMIN_USERNAME": a.get("admin_user", "admin")})
    (PROJECT / ".env").write_text("# DiagTrack settings, written by setup. No passwords here: they are in ./secrets.\n" +
                                  "\n".join(f"{k}={v}" for k, v in env.items()) + "\n")
    for folder in ("data/media", "data/status", "data/backups", "data/backups2", "data/import"):
        (PROJECT / folder).mkdir(parents=True, exist_ok=True)
    os.environ["FIELD_KEY"] = field_key
    dests = [{"id": "dest1", "name": "Main backup folder", "path": "/backups"}]
    if a["backup_dir_2"]:
        dests.append({"id": "dest2", "name": "Second backup folder", "path": "/backups2"})
    for d in dests:
        keep_daily = old_env.get("BACKUP_KEEP_DAYS", "")
        d.update(enabled=True, frequency="daily", every_hours=6, time=a["backup_time"], weekday=6, monthday=1,
                 keep_daily=int(keep_daily) if keep_daily.isdigit() else 14, keep_weekly=8, keep_monthly=12, keep_manual_days=30, alert_after_hours=26,
                 require_marker=d["id"] == "dest1" and old_env.get("BACKUP_REQUIRE_MARKER") == "1",
                 nas_user="", nas_password="")
    STATUS.mkdir(parents=True, exist_ok=True)
    (STATUS / "backup-config.json").write_text(json.dumps({
        "destinations": dests, "alert_users": [], "verify_days": 7, "test_restore_days": 30,
        "created": time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime())}, indent=1))
    if mode == "move":
        (STATUS / "jobs").mkdir(parents=True, exist_ok=True)
        job_id = uuid.uuid4().hex[:12]
        (STATUS / "jobs" / f"{job_id}.json").write_text(json.dumps({
            "id": job_id, "kind": "restore", "params": {"path": f"/import/{a['move_file'].name}", "site_url": a["site_url"]}, "by": "setup",
            "status": "queued", "percent": 0, "step": "Waiting for the backup container", "message": "", "result": {},
            "created": time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime()), "started": None, "finished": None,
            "log": []}, indent=1))
    steps.ok("Settings in .env, secrets in ./secrets, backup schedule written." +
             (" Passwords removed from .env." if mode == "upgrade" else ""))

    steps.start("next")
    ui.say("Now run, in this folder:")
    ui.say(ui.bold("    docker compose up -d"))
    if mode == "upgrade":
        ui.say("On first start DiagTrack updates its database to this version and makes its first encrypted backup.")
        ui.say("Backup files made by the earlier version stay in the backup folder; delete them once the new ones")
        ui.say("have run for a month.")
    ui.say("The first start builds DiagTrack and takes a few minutes." +
           (" The backup container then restores the move file; staff see 'Back shortly' until it is done." if mode == "move" else ""))
    ui.say("Then check everything with:")
    ui.say(ui.bold("    docker compose run --rm setup verify"))
    common.offer_recovery_sheet({"Address": a["site_url"], "Backup passphrase": a["backup_passphrase"],
                                 "Database password": read_secret("db_password"),
                                 "Secrets folder": str(Path(".") / "secrets") + " (never delete it)"})
    return 0


def verify():
    common.app_dir("/")
    os.environ.setdefault("DIAGTRACK_SECRETS_DIR", str(SECRETS))
    ui.banner("DiagTrack check (Docker)")
    steps = ui.Steps({"web": "Screens", "db": "Database and worker", "backup": "A backup through the backup container"})
    steps.start("web")
    end = time.time() + 180
    while True:
        try:
            with urllib.request.urlopen(os.environ.get("DIAGTRACK_WEB_URL", "http://web:8000") + "/healthz", timeout=5) as r:
                if r.status == 200:
                    break
        except Exception:
            pass
        if time.time() > end:
            raise Failed("DT-V11", "The web container does not answer.", todo="Run: docker compose ps, then "
                                                                              "docker compose logs --tail 50 web")
        time.sleep(3)
    steps.ok("The screens answer.")
    steps.start("db")
    import subprocess
    manage = Path("/app/manage.py") if Path("/app/manage.py").exists() else PROJECT / "app" / "manage.py"
    proc = subprocess.run([sys.executable, str(manage), "setupcheck"], capture_output=True, text=True)
    if proc.returncode != 0:
        raise Failed("DT-V12", "The database check failed.", proc.stderr.strip()[-300:])
    res = json.loads(proc.stdout.strip().splitlines()[-1])
    steps.info(f"{res['tickets']} tickets, {res['admins']} administrator(s), DiagTrack {res['version']}.")
    if res.get("worker_age") is None or res["worker_age"] > 300:
        raise Failed("DT-V13", "The worker container has not reported in.", todo="docker compose logs --tail 50 worker")
    steps.ok("Database and worker are fine.")
    steps.start("backup")
    jobs = STATUS / "jobs"
    jobs.mkdir(parents=True, exist_ok=True)
    job_id = uuid.uuid4().hex[:12]
    path = jobs / f"{job_id}.json"
    path.write_text(json.dumps({"id": job_id, "kind": "backup", "params": {}, "by": "setup verify", "status": "queued",
                                "percent": 0, "step": "Waiting", "message": "", "result": {},
                                "created": time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime()),
                                "started": None, "finished": None, "log": []}))
    end = time.time() + 1800
    while time.time() < end:
        job = json.loads(path.read_text())
        if job["status"] == "done":
            steps.ok(job["message"])
            ui.say("")
            ui.say(ui.green("Everything works. Open the address from setup in a browser and sign in."))
            return 0
        if job["status"] == "failed":
            raise Failed("DT-V14", f"The backup failed: {job['message']}",
                         todo="Check the backup folder in .env (BACKUP_DIR), then docker compose up -d and try again.")
        time.sleep(3)
    raise Failed("DT-V15", "The backup container did not pick up the task.", todo="docker compose logs --tail 50 backup")
