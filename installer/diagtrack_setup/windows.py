"""
Native Windows install: Windows 11, Windows Server 2016, 2019, 2022, 2025.

Program:   C:\\Program Files\\DiagTrack      (app, python, service wrapper)
Data:      C:\\ProgramData\\DiagTrack        (settings, encrypted secrets, attachments, status, logs)
Database:  PostgreSQL 17 as its own Windows service, reachable only from this computer
Service:   "DiagTrack", runs `manage.py serve` as the virtual account NT SERVICE\\DiagTrack
"""
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

from . import checks, common, ui
from .ui import Failed

PROGRAM = Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "DiagTrack"
DATA = Path(os.environ.get("ProgramData", r"C:\ProgramData")) / "DiagTrack"
SERVICE = "DiagTrack"
TASK = "DiagTrack backup safety net"
FIREWALL_RULE = "DiagTrack web"
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
SID_SYSTEM, SID_ADMINS = "*S-1-5-18", "*S-1-5-32-544"


# --------------------------------------------------------------------------
# Small helpers
# --------------------------------------------------------------------------
def run(args, what, code, env=None, check=True, timeout=None, cwd=None):
    ui.log(f"RUN {' '.join(str(a) for a in args)}")
    try:
        proc = subprocess.run([str(a) for a in args], capture_output=True, text=True, env=env, timeout=timeout,
                              cwd=cwd, creationflags=NO_WINDOW, errors="replace")
    except FileNotFoundError as exc:
        raise Failed(code, f"{what}: a program was not found ({exc.filename}).") from None
    except subprocess.TimeoutExpired:
        raise Failed(code, f"{what} took too long and was stopped.") from None
    out = (proc.stdout or "") + (proc.stderr or "")
    ui.log(out[-4000:])
    if check and proc.returncode != 0:
        tail = "\n".join(out.strip().splitlines()[-6:])
        raise Failed(code, f"{what} failed.", ui.redact(tail) or f"exit code {proc.returncode}")
    return proc


def powershell(command, what, code, check=True):
    return run(["powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command", command],
               what, code, check=check)


def service_state(name):
    proc = subprocess.run(["sc.exe", "query", name], capture_output=True, text=True, creationflags=NO_WINDOW)
    if proc.returncode != 0:
        return None
    for line in proc.stdout.splitlines():
        if "STATE" in line:
            return line.split()[-1]
    return "UNKNOWN"


def wait_service(name, wanted="RUNNING", seconds=90):
    end = time.time() + seconds
    while time.time() < end:
        if service_state(name) == wanted:
            return True
        time.sleep(2)
    return False


def download(url, target, what, code):
    """Through PowerShell, so Windows' own certificate store and proxy settings are used (older servers included)."""
    ui.log(f"DOWNLOAD {url}")
    proc = powershell(
        "[Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor "
        "[Net.SecurityProtocolType]::Tls12; $ProgressPreference = 'SilentlyContinue'; "
        f"Invoke-WebRequest -Uri '{url}' -OutFile '{target}' -UseBasicParsing", f"Downloading {what}", code, check=False)
    if proc.returncode != 0 or not Path(target).exists() or Path(target).stat().st_size < 1024 * 1024:
        raise Failed(code, f"Downloading {what} failed.", "No internet, or a proxy or firewall blocks the site.",
                     f"Allow {url.split('/')[2]} through the proxy and run setup again.")


def check_signature(path, signer, code):
    """Windows Authenticode: the file must carry a valid signature from the expected publisher."""
    proc = powershell(f"$s = Get-AuthenticodeSignature -FilePath '{path}'; \"$($s.Status)|$($s.SignerCertificate.Subject)\"",
                      "Checking the download's signature", code)
    status, _, subject = proc.stdout.strip().partition("|")
    if status != "Valid" or signer.lower() not in subject.lower():
        raise Failed(code, f"The downloaded file is not signed by {signer} (status {status}).",
                     "The download was damaged or replaced on the way.", "Run setup again; if it repeats, tell your IT team.")


def versions():
    return json.loads((Path(__file__).resolve().parent.parent / "versions.json").read_text())


def python_exe():
    return Path(sys.executable)


def manage(args, what, code, env_extra=None, check=True, timeout=None):
    env = dict(os.environ, DIAGTRACK_HOME=str(DATA), PYTHONUNBUFFERED="1")
    env.update(env_extra or {})
    return run([python_exe(), PROGRAM / "app" / "manage.py", *args], what, code, env=env, check=check, timeout=timeout,
               cwd=PROGRAM / "app")


def load_state():
    try:
        return json.loads((DATA / "setup-state.json").read_text())
    except (OSError, ValueError):
        return {}


def save_state(st):
    DATA.mkdir(parents=True, exist_ok=True)
    (DATA / "setup-state.json").write_text(json.dumps(st, indent=1))


def runtime():
    common.app_dir(PROGRAM if (PROGRAM / "app" / "manage.py").exists() else SOURCE)
    from config import runtime as rt
    return rt


SOURCE = Path(__file__).resolve().parent.parent.parent


def load_secrets():
    return runtime().load_secrets_file(DATA / "secrets.dat")


def save_secrets(values):
    rt = runtime()
    DATA.mkdir(parents=True, exist_ok=True)
    current = rt.load_secrets_file(DATA / "secrets.dat") if (DATA / "secrets.dat").exists() else {}
    current.update({k: v for k, v in values.items() if v is not None})
    rt.save_secrets(DATA / "secrets.dat", current)
    for v in current.values():
        ui.hide(v)


# --------------------------------------------------------------------------
# Questions
# --------------------------------------------------------------------------
def suggest_backup_folder():
    for drive in ("D:", "E:", "F:"):
        if os.path.exists(drive + "\\"):
            return f"{drive}\\DiagTrack-Backups"
    return r"C:\DiagTrack-Backups"


def ask_folder(label, default, key_prefix, optional=False):
    hint = r"A local folder such as D:\DiagTrack-Backups, or a network share such as \\nas01\backups\diagtrack"
    path = ui.ask(label, default, hint=hint, key=f"{key_prefix}_path",
                  check=lambda v: None if (v or optional) else "Type a folder.")
    if not path:
        return None
    dest = {"path": path, "nas_user": "", "nas_password": ""}
    if path.startswith("\\\\"):
        if ui.yes("Does this network share need a user name and password?", True, key=f"{key_prefix}_needs_login"):
            dest["nas_user"] = ui.ask("NAS user name (for example NAS01\\backup or backup)", key=f"{key_prefix}_nas_user",
                                      check=lambda v: None if v else "Type the user name.")
            dest["nas_password"] = ui.ask_secret("NAS password", confirm=False, key=f"{key_prefix}_nas_password")
    return dest


def questions(mode, pg_existing):
    a = {"mode": mode}
    ui.section("Questions")
    ui.say("Answer these now; nothing is installed until you confirm the summary.")
    if mode == "new":
        a["admin_user"] = ui.ask("DiagTrack administrator user name", "admin", key="admin_user",
                                 check=lambda v: None if v.replace(".", "").replace("_", "").isalnum() else
                                 "Use letters, digits, dots or underscores.")
        a["admin_password"] = ui.ask_secret("Administrator password (10 or more characters, letters and a digit)",
                                            check=ui.password_problem, key="admin_password")
    else:
        a["move_file"] = ui.ask("Full path of the move file from the old server", key="move_file",
                                hint=r"For example E:\DiagTrack-Move\diagtrack-pramodini-20261020-210500-full.dtbak",
                                check=lambda v: None if Path(v).is_file() else "That file was not found.")
        while True:
            pp = ui.ask_secret("Backup passphrase of the old server (from its recovery sheet)", confirm=False,
                               key="backup_passphrase")
            try:
                summary = common.read_move_file(a["move_file"], pp)
                break
            except Failed as f:
                if ui.ANSWERS:
                    raise
                ui.say(ui.red(f"  {f.what}"))
        a["backup_passphrase"] = pp
        a["move_summary"] = summary
        ui.say(ui.green(f"  The file opens: {common.describe_summary(summary)}."))

    used = checks.port_free(80)
    a["port"] = int(ui.ask("Web port staff will use", "80" if used else "8080", key="port",
                           hint="" if used else "Port 80 is in use on this computer (often IIS), so 8080 is suggested.",
                           check=lambda v: None if v.isdigit() and 0 < int(v) < 65536 and checks.port_free(int(v))
                           else "That port is not free on this computer. Try 8080 or 8443."))
    host = socket.gethostname().lower()
    default_url = f"http://{host}" + ("" if a["port"] == 80 else f":{a['port']}")
    a["site_url"] = ui.ask("Address staff will type in the browser", default_url, key="site_url",
                           hint="Used in email links and on equipment QR labels. Your network team can give the server a name.",
                           check=lambda v: None if v.startswith(("http://", "https://")) else "Start with http://")

    a["backup1"] = ask_folder("Backup folder", suggest_backup_folder(), "backup1")
    if ui.yes("Add a second backup folder, for example another centre's NAS?", False, key="backup2_wanted"):
        a["backup2"] = ask_folder("Second backup folder", "", "backup2")
    a["backup_time"] = ui.ask("Daily backup time (24-hour clock)", "01:30", key="backup_time",
                              check=lambda v: None if len(v) == 5 and v[2] == ":" and v[:2].isdigit() and v[3:].isdigit()
                              and int(v[:2]) < 24 and int(v[3:]) < 60 else "Use a time like 01:30.")
    if mode == "new":
        ui.say("")
        ui.say("Backups are always encrypted. Choose a passphrase: a short sentence is best. It is printed on the")
        ui.say("recovery sheet at the end. Without it nobody can open the backups, so keep that sheet safe.")
        a["backup_passphrase"] = ui.ask_secret("Backup passphrase (12 or more characters)",
                                               check=common.passphrase_problem, key="backup_passphrase")
    if pg_existing and not pg_existing.get("ours"):
        ui.say("")
        ui.say(f"PostgreSQL 17 is already installed here ({pg_existing['service']}). Setup needs its 'postgres' password")
        ui.say("once, to create DiagTrack's own database and login. It is not kept.")
        a["pg_super"] = ui.ask_secret("Password of the 'postgres' user", confirm=False, key="pg_super")
    return a


def summary(a, info):
    ui.section("Summary")
    rows = [("Windows", info["os"]), ("Install", "Move in from another server" if a["mode"] == "move" else "New install"),
            ("Program folder", str(PROGRAM)), ("Data folder", str(DATA)),
            ("Web address", f"{a['site_url']} (port {a['port']})"),
            ("Backup folder", a["backup1"]["path"])]
    if a.get("backup2"):
        rows.append(("Second backup folder", a["backup2"]["path"]))
    rows.append(("Daily backup at", a["backup_time"]))
    if a["mode"] == "new":
        rows.append(("Administrator", a["admin_user"]))
    else:
        rows.append(("Move file", common.describe_summary(a["move_summary"])))
    rows.append(("Will be installed", info["to_install"]))
    for k, v in rows:
        ui.say(f"  {k + ':':<22}{v}")
    ui.say("")
    if not ui.yes("Install DiagTrack with these settings?", True, key="confirm"):
        raise Failed("DT-Q09", "Cancelled at the summary. Nothing was installed.")


# --------------------------------------------------------------------------
# PostgreSQL
# --------------------------------------------------------------------------
def find_postgres():
    v = versions()["postgresql"]
    st = load_state().get("pg", {})
    state_ = service_state(v["service"])
    if not state_:
        return None
    return {"service": v["service"], "prefix": st.get("prefix", v["prefix"]), "port": st.get("port", 5432),
            "ours": bool(st.get("ours"))}


def install_postgres(secrets_, steps):
    v = versions()["postgresql"]
    port = checks.first_free_port([5432, 5433, 5434, 5435])
    if not port:
        raise Failed("DT-S04", "No free port for PostgreSQL (5432 to 5435 are all in use).")
    work = Path(tempfile.mkdtemp(prefix="diagtrack-"))
    exe = work / "postgresql-17-windows-x64.exe"
    last = None
    for url in v["urls"]:
        try:
            download(url, exe, f"PostgreSQL {v['version']}", "DT-S04")
            check_signature(exe, v["signer"], "DT-S04")
            break
        except Failed as f:
            last = f
    else:
        raise last
    steps.info(f"Installing PostgreSQL {v['version']} as the Windows service {v['service']} on port {port}...")
    options = work / "options.ini"
    options.write_text("\n".join([
        "mode=unattended", "unattendedmodeui=none", f"superpassword={secrets_['PG_SUPERPASSWORD']}",
        f"serverport={port}", f"prefix={v['prefix']}", f"datadir={v['prefix']}\\data", f"servicename={v['service']}",
        "disable-components=pgAdmin,stackbuilder", "install_runtimes=1", ""]), encoding="utf-8")
    try:
        run([exe, "--optionfile", options], "Installing PostgreSQL", "DT-S04", timeout=1800)
    finally:
        options.unlink(missing_ok=True)
        shutil.rmtree(work, ignore_errors=True)
    if not wait_service(v["service"], "RUNNING", 120):
        raise Failed("DT-S04", "PostgreSQL was installed but its service did not start.",
                     "Another program may be using the port, or the disk is full.",
                     f"Open Services, start '{v['service']}', then run setup again.")
    st = load_state()
    st["pg"] = {"service": v["service"], "prefix": v["prefix"], "port": port, "ours": True}
    save_state(st)
    return st["pg"]


def pg_connect(pg, password, user="postgres", dbname="postgres"):
    import psycopg
    return psycopg.connect(host="127.0.0.1", port=pg["port"], user=user, password=password, dbname=dbname,
                           autocommit=True, connect_timeout=10)


def create_database(pg, super_pw, db_pw):
    from psycopg import sql
    try:
        conn = pg_connect(pg, super_pw)
    except Exception as exc:
        raise Failed("DT-S06", "Could not sign in to PostgreSQL as 'postgres'.", str(exc).strip().splitlines()[0],
                     "If PostgreSQL was installed before DiagTrack, run setup again and type its 'postgres' password.")
    with conn:
        exists = conn.execute("SELECT 1 FROM pg_roles WHERE rolname = 'diagtrack'").fetchone()
        verb = "ALTER" if exists else "CREATE"
        conn.execute(sql.SQL(verb + " ROLE diagtrack WITH LOGIN CREATEDB PASSWORD {}").format(sql.Literal(db_pw)))
        if not conn.execute("SELECT 1 FROM pg_database WHERE datname = 'diagtrack'").fetchone():
            conn.execute("CREATE DATABASE diagtrack OWNER diagtrack ENCODING 'UTF8' TEMPLATE template0")


# --------------------------------------------------------------------------
# Files, service, firewall, task, shortcuts
# --------------------------------------------------------------------------
IGNORE = shutil.ignore_patterns("__pycache__", "*.pyc", "media", "status", "staticfiles", "db.sqlite3", "*.log")


def copy_program(source):
    """Copy the new version beside the old one, then switch, keeping the old one as app.previous."""
    PROGRAM.mkdir(parents=True, exist_ok=True)
    new = PROGRAM / "app.new"
    shutil.rmtree(new, ignore_errors=True)
    shutil.copytree(Path(source) / "app", new, ignore=IGNORE)
    current, previous = PROGRAM / "app", PROGRAM / "app.previous"
    if current.exists():
        shutil.rmtree(previous, ignore_errors=True)
        current.rename(previous)
    new.rename(current)
    for name in ("installer",):
        target = PROGRAM / name
        if Path(source).resolve() != PROGRAM.resolve():
            shutil.rmtree(target, ignore_errors=True)
            shutil.copytree(Path(source) / name, target, ignore=IGNORE)
    if (Path(source) / "Setup.cmd").exists() and Path(source).resolve() != PROGRAM.resolve():
        shutil.copy2(Path(source) / "Setup.cmd", PROGRAM / "Setup.cmd")
    shutil.copy2(Path(source) / "installer" / "windows" / "DiagTrackService.exe", PROGRAM / "DiagTrackService.exe")


def install_packages(code="DT-S05"):
    run([python_exe(), "-m", "pip", "install", "--disable-pip-version-check", "--no-warn-script-location", "-q",
         "-r", PROGRAM / "app" / "requirements.txt"], "Installing DiagTrack's Python packages", code, timeout=1800)


def write_conf(a, pg):
    rt = runtime()
    values = {
        "DIAGTRACK_INSTALL": "windows", "DB_HOST": "127.0.0.1", "DB_PORT": str(pg["port"]), "DB_NAME": "diagtrack",
        "DB_USER": "diagtrack", "PG_BIN": f"{pg['prefix']}\\bin", "HTTP_PORT": str(a["port"]), "LISTEN_HOST": "0.0.0.0",
        "SITE_URL": a["site_url"], "TIME_ZONE": "Asia/Kolkata", "STATIC_ROOT": str(PROGRAM / "staticfiles"),
        "ALLOWED_HOSTS": "*",
    }
    rt.write_conf(DATA / "diagtrack.conf", values,
                  header="DiagTrack settings. Passwords are not here: they are in secrets.dat, encrypted for this computer.\n"
                         "After a change, restart the DiagTrack service.")


def backup_config(a, secrets_):
    common.app_dir(PROGRAM)
    from tickets import crypto
    os.environ["FIELD_KEY"] = secrets_["FIELD_KEY"]
    dests = []
    for i, key in enumerate(("backup1", "backup2")):
        d = a.get(key)
        if not d:
            continue
        dests.append({
            "id": f"dest{i + 1}", "name": "Main backup folder" if i == 0 else "Second backup folder", "path": d["path"],
            "enabled": True, "frequency": "daily", "every_hours": 6, "time": a["backup_time"], "weekday": 6, "monthday": 1,
            "keep_daily": 14, "keep_weekly": 8, "keep_monthly": 12, "keep_manual_days": 30, "alert_after_hours": 26,
            "require_marker": False, "nas_user": d.get("nas_user", ""),
            "nas_password": crypto.encrypt(d["nas_password"]) if d.get("nas_password") else ""})
    status = DATA / "status"
    status.mkdir(parents=True, exist_ok=True)
    path = status / "backup-config.json"
    cfg = json.loads(path.read_text()) if path.exists() else {"alert_users": [], "verify_days": 7, "test_restore_days": 30}
    cfg["destinations"] = dests
    cfg.setdefault("created", time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime()))
    path.write_text(json.dumps(cfg, indent=1))


def service_xml(pg):
    log = DATA / "logs"
    return f"""<service>
  <id>{SERVICE}</id>
  <name>DiagTrack</name>
  <description>DiagTrack service desk: screens, background worker and backups.</description>
  <executable>{python_exe()}</executable>
  <arguments>"{PROGRAM / 'app' / 'manage.py'}" serve</arguments>
  <workingdirectory>{PROGRAM / 'app'}</workingdirectory>
  <env name="DIAGTRACK_HOME" value="{DATA}"/>
  <env name="PYTHONUNBUFFERED" value="1"/>
  <startmode>Automatic</startmode>
  <delayedAutoStart>true</delayedAutoStart>
  <depend>{pg['service']}</depend>
  <onfailure action="restart" delay="10 sec"/>
  <onfailure action="restart" delay="30 sec"/>
  <onfailure action="restart" delay="60 sec"/>
  <resetfailure>1 hour</resetfailure>
  <stoptimeout>30 sec</stoptimeout>
  <logpath>{log}</logpath>
  <log mode="roll-by-size">
    <sizeThreshold>10240</sizeThreshold>
    <keepFiles>8</keepFiles>
  </log>
</service>
"""


def stop_service():
    if service_state(SERVICE) not in (None, "STOPPED"):
        run(["sc.exe", "stop", SERVICE], "Stopping DiagTrack", "DT-S07", check=False)
        wait_service(SERVICE, "STOPPED", 60)


def install_service(pg):
    wrapper = PROGRAM / "DiagTrackService.exe"
    (PROGRAM / "DiagTrackService.xml").write_text(service_xml(pg), encoding="utf-8")
    (DATA / "logs").mkdir(parents=True, exist_ok=True)
    if service_state(SERVICE) is not None:
        stop_service()
        run([wrapper, "uninstall"], "Removing the old DiagTrack service", "DT-S07", check=False)
        time.sleep(2)
    run([wrapper, "install"], "Registering the DiagTrack service", "DT-S07")
    # A virtual service account: no password, only the rights given below.
    run(["sc.exe", "config", SERVICE, "obj=", f"NT SERVICE\\{SERVICE}"],
        "Setting the DiagTrack service account", "DT-S07")


def set_permissions():
    """Data folder: SYSTEM and Administrators full control, the DiagTrack service may change files, nobody else."""
    DATA.mkdir(parents=True, exist_ok=True)
    # The folder itself: no inherited rights, only these three.
    run(["icacls", DATA, "/inheritance:r", "/grant:r", f"{SID_SYSTEM}:(OI)(CI)F", f"{SID_ADMINS}:(OI)(CI)F",
         f"NT SERVICE\\{SERVICE}:(OI)(CI)M", "/C", "/Q"], "Setting permissions on the data folder", "DT-S07")
    # Everything inside: inherit exactly those rights from the folder.
    run(["icacls", str(DATA / "*"), "/reset", "/T", "/C", "/Q"], "Setting permissions inside the data folder", "DT-S07")


def firewall(port):
    run(["netsh", "advfirewall", "firewall", "delete", "rule", f"name={FIREWALL_RULE}"], "Firewall", "DT-S08", check=False)
    run(["netsh", "advfirewall", "firewall", "add", "rule", f"name={FIREWALL_RULE}", "dir=in", "action=allow",
         "protocol=TCP", f"localport={port}", "profile=any"], "Opening the web port in Windows Firewall", "DT-S08")


def safety_task():
    cmd = PROGRAM / "backup-if-overdue.cmd"
    cmd.write_text("\r\n".join([
        "@echo off",
        "rem Daily safety net: backs up only if a destination has missed its schedule (for example the service was stopped).",
        f'set "DIAGTRACK_HOME={DATA}"',
        f'"{python_exe()}" "{PROGRAM / "app" / "manage.py"}" backup --if-overdue >> "{DATA / "logs" / "safety-net.log"}" 2>&1',
        ""]), encoding="utf-8")
    powershell(
        f"$a = New-ScheduledTaskAction -Execute '{cmd}'; "
        "$t = New-ScheduledTaskTrigger -Daily -At '06:15'; "
        "$p = New-ScheduledTaskPrincipal -UserId 'SYSTEM' -LogonType ServiceAccount -RunLevel Highest; "
        "$s = New-ScheduledTaskSettingsSet -StartWhenAvailable -ExecutionTimeLimit (New-TimeSpan -Hours 6); "
        f"Register-ScheduledTask -TaskName '{TASK}' -Action $a -Trigger $t -Principal $p -Settings $s -Force | Out-Null",
        "Creating the backup safety-net task", "DT-S08")


def command_line():
    """C:\\Program Files\\DiagTrack\\diagtrack.cmd: `diagtrack backup`, `diagtrack restore ...` in an admin prompt."""
    (PROGRAM / "diagtrack.cmd").write_text("\r\n".join([
        "@echo off",
        "rem DiagTrack commands. Open Command Prompt as administrator, then for example:",
        "rem   diagtrack backup            diagtrack backup --list         diagtrack restore --dest dest1",
        "rem   diagtrack verifybackup --dest dest1 --test-restore           diagtrack changepassword admin",
        f'set "DIAGTRACK_HOME={DATA}"',
        f'"{python_exe()}" "{PROGRAM / "app" / "manage.py"}" %*',
        ""]), encoding="utf-8")
    # Put the folder on the system PATH once, so `diagtrack` works in any new administrator prompt.
    powershell(
        "$p = [Environment]::GetEnvironmentVariable('Path', 'Machine'); "
        f"if (($p -split ';') -notcontains '{PROGRAM}') {{ [Environment]::SetEnvironmentVariable('Path', $p + ';{PROGRAM}', 'Machine') }}",
        "Adding the diagtrack command", "DT-S08", check=False)


def shortcuts(site_url):
    desktop = Path(os.environ.get("PUBLIC", r"C:\Users\Public")) / "Desktop"
    try:
        desktop.mkdir(parents=True, exist_ok=True)
        (desktop / "Open DiagTrack.url").write_text(f"[InternetShortcut]\r\nURL={site_url}/\r\n", encoding="utf-8")
        (desktop / "DiagTrack Backups.url").write_text(f"[InternetShortcut]\r\nURL={site_url}/settings/backups/\r\n",
                                                        encoding="utf-8")
    except OSError as exc:
        ui.log(f"Shortcuts not created: {exc}")


def wait_http(port, seconds=120):
    end = time.time() + seconds
    while time.time() < end:
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/healthz", timeout=5) as r:
                if r.status == 200:
                    return True
        except Exception:
            pass
        time.sleep(3)
    return False


def start_service(port):
    run(["sc.exe", "start", SERVICE], "Starting DiagTrack", "DT-S09", check=False)
    if not wait_http(port, 150):
        raise Failed("DT-S09", "The DiagTrack service did not answer after starting.",
                     "See the newest file in the logs folder for the reason.",
                     f"Open {DATA / 'logs'}, read the end of DiagTrack.err.log, then run setup and choose Repair.")


# --------------------------------------------------------------------------
# The whole install
# --------------------------------------------------------------------------
TITLES = {"pre": "Checking this computer", "ask": "Questions and summary", "secrets": "Creating the passwords",
          "postgres": "PostgreSQL 17", "files": "DiagTrack program and packages", "database": "Preparing the database",
          "service": "Windows service, firewall and safety net", "verify": "Checking that everything works",
          "report": "Report"}


def install(source, mode="new"):
    steps = ui.Steps(TITLES)
    steps.start("pre")
    os_name, build, is_server = checks.check_windows()
    steps.info(os_name)
    problems, notes = checks.basics(str(DATA))
    for n in notes:
        steps.info(n)
    if problems:
        raise Failed("DT-P04", problems[0], todo="Free up disk space or add memory, then run setup again.")
    pg = find_postgres()
    to_install = []
    if not pg:
        to_install.append(f"PostgreSQL {versions()['postgresql']['version']}")
    to_install.append("DiagTrack service")
    if not pg and not checks.reachable("https://get.enterprisedb.com/"):
        steps.warn("get.enterprisedb.com cannot be reached; PostgreSQL will be downloaded from the backup address.")
    if not checks.reachable("https://pypi.org/simple/django/"):
        raise Failed("DT-P05", "pypi.org cannot be reached.", "No internet, or a proxy blocks it.",
                     "Allow pypi.org and files.pythonhosted.org through the proxy.")
    steps.ok("This computer can run DiagTrack.")

    steps.start("ask")
    a = questions(mode, pg)
    summary(a, {"os": os_name, "to_install": ", ".join(to_install)})
    steps.ok()

    steps.start("secrets")
    existing = load_secrets() if (DATA / "secrets.dat").exists() else {}
    secrets_ = {
        "SECRET_KEY": existing.get("SECRET_KEY") or common.new_password(50),
        "DB_PASSWORD": existing.get("DB_PASSWORD") or common.new_password(32),
        "FIELD_KEY": existing.get("FIELD_KEY") or common.new_key(),
        "PG_SUPERPASSWORD": a.get("pg_super") or existing.get("PG_SUPERPASSWORD") or common.new_password(32),
        "BACKUP_PASSPHRASE": a["backup_passphrase"],
    }
    if mode == "new":
        common.app_dir(source)
        secrets_["ADMIN_PASSWORD_HASH"] = common.password_hash(a["admin_password"])
    save_secrets(secrets_)
    save_state(dict(load_state(), started=time.strftime("%Y-%m-%d %H:%M"), answers={
        k: v for k, v in a.items() if k not in ("admin_password", "backup_passphrase", "pg_super", "move_summary")
        and not isinstance(v, dict)}))
    steps.ok("Passwords created and stored encrypted for this computer.")

    steps.start("postgres", 6)
    if pg:
        steps.info(f"Using the PostgreSQL already installed ({pg['service']}, port {pg['port']}).")
        if not pg["ours"]:
            st = load_state()
            st["pg"] = pg
            save_state(st)
    else:
        pg = install_postgres(secrets_, steps)
    create_database(pg, secrets_["PG_SUPERPASSWORD"], secrets_["DB_PASSWORD"])
    steps.ok(f"Database 'diagtrack' ready on port {pg['port']}.")

    steps.start("files", 3)
    stop_service()
    copy_program(source)
    install_packages()
    (DATA / "media").mkdir(parents=True, exist_ok=True)
    write_conf(a, pg)
    backup_config(a, secrets_)
    steps.ok(f"Program in {PROGRAM}, data in {DATA}.")

    steps.start("database", 2)
    manage(["migrate", "--noinput"], "Creating the database tables", "DT-S06")
    manage(["collectstatic", "--noinput"], "Preparing the screens", "DT-S06")
    if mode == "move":
        steps.info("Restoring the move file. This can take a while for many attachments...")
        manage(["restore", a["move_file"], "--yes"], "Restoring the move file", "DT-M03", timeout=6 * 3600)
        steps.info(common.describe_summary(a["move_summary"]))
    manage(["setup_initial"], "Adding the starting departments and the administrator", "DT-S06",
           env_extra={"ADMIN_USERNAME": a.get("admin_user", "admin")})
    manage(["siteconfig", "--site-url", a["site_url"]], "Saving the address", "DT-S06")
    steps.ok("Database ready.")

    steps.start("service")
    install_service(pg)
    set_permissions()
    firewall(a["port"])
    safety_task()
    command_line()
    shortcuts(a["site_url"])
    start_service(a["port"])
    steps.ok(f"The DiagTrack service is running and starts with Windows. Port {a['port']} is open in the firewall.")

    steps.start("verify", 2)
    result = verify(a, steps)
    st = load_state()
    st.update(installed=True, version=(PROGRAM / "app" / "VERSION").read_text().strip(),
              installed_at=time.strftime("%Y-%m-%d %H:%M"), port=a["port"], site_url=a["site_url"])
    save_state(st)

    steps.start("report")
    report(a, pg, result)
    common.offer_recovery_sheet({
        "Server": socket.gethostname(), "Address": a["site_url"], "DiagTrack version": st["version"],
        "Backup passphrase": a["backup_passphrase"], "Database password": secrets_["DB_PASSWORD"],
        "PostgreSQL 'postgres'": secrets_["PG_SUPERPASSWORD"] if pg.get("ours") else "(installed before DiagTrack)",
        "Backup folder": a["backup1"]["path"],
    })
    return 0


def verify(a, steps):
    env = {}
    args = ["setupcheck"]
    if a.get("admin_password"):
        args += ["--admin", a["admin_user"]]
        env["ADMIN_CHECK_PASSWORD"] = a["admin_password"]
    proc = manage(args, "Checking the installation", "DT-V01", env_extra=env)
    res = json.loads(proc.stdout.strip().splitlines()[-1])
    if not res.get("db"):
        raise Failed("DT-V01", "The database check failed.")
    steps.info(f"Database: {res['tickets']} tickets, {res['admins']} active administrator(s).")
    if a.get("admin_password") and not res.get("admin_ok"):
        raise Failed("DT-V02", "The administrator cannot sign in with the chosen password.")
    steps.info("Screens answer on " + a["site_url"])
    end = time.time() + 120
    while time.time() < end:
        res = json.loads(manage(["setupcheck"], "Checking the worker", "DT-V03").stdout.strip().splitlines()[-1])
        if res.get("worker_age") is not None and res["worker_age"] < 180:
            break
        time.sleep(5)
    else:
        raise Failed("DT-V03", "The background worker did not report in.", todo="Restart the DiagTrack service.")
    steps.info("Background worker is running.")
    proc = manage(["backup"], "Making the first backup", "DT-V04", timeout=3600, check=False)
    if proc.returncode != 0:
        tail = "\n".join((proc.stdout + proc.stderr).strip().splitlines()[-3:])
        raise Failed("DT-V04", "The first backup failed.", ui.redact(tail),
                     "Fix the backup folder on the Backups page (Check folder), then run setup and choose Repair.")
    steps.info("First backup written to " + a["backup1"]["path"] + " and checked.")
    steps.ok("Everything works.")
    return res


def report(a, pg, res):
    lines = [
        "DiagTrack setup report",
        f"Date:                {time.strftime('%d %b %Y %H:%M')}",
        f"Address:             {a['site_url']}",
        f"Sign in as:          {a.get('admin_user', '(the administrators of the old server)')}",
        f"Program folder:      {PROGRAM}",
        f"Data folder:         {DATA}",
        f"Logs:                {DATA / 'logs'}",
        f"Database:            PostgreSQL {versions()['postgresql']['version']}, service {pg['service']}, port {pg['port']}",
        f"Windows service:     {SERVICE} (starts automatically; runs as NT SERVICE\\{SERVICE})",
        f"Backups:             daily at {a['backup_time']} to {a['backup1']['path']}"
        + (f" and {a['backup2']['path']}" if a.get("backup2") else ""),
        f"Safety net:          Task Scheduler '{TASK}', daily 06:15",
        f"Commands:            {PROGRAM / 'diagtrack.cmd'} (type `diagtrack backup` in an administrator prompt)",
        "",
        "Next steps",
        "  1. Open the address above and sign in.",
        "  2. Settings and health: check the four boxes are green; fill in the mail server.",
        "  3. Backups page: check the folders and who is alerted.",
        "  4. Keep the recovery sheet safe.",
    ]
    text = "\n".join(lines) + "\n"
    (DATA / "setup-report.txt").write_text(text, encoding="utf-8")
    ui.section("Done")
    for line in lines:
        ui.say(line)
    ui.say("")
    ui.say(f"This report is saved as {DATA / 'setup-report.txt'}.")


# --------------------------------------------------------------------------
# Update, repair, remove
# --------------------------------------------------------------------------
def update(source):
    steps = ui.Steps({"backup": "Backing up first", "files": "Installing the new version",
                      "database": "Updating the database", "start": "Starting and checking"})
    st = load_state()
    port = st.get("port", 80)
    new_version = (Path(source) / "app" / "VERSION").read_text().strip()
    ui.say(f"Installed: {st.get('version', '?')}   New: {new_version}")
    if not ui.yes("Update now? Staff are interrupted for a few minutes.", True, key="confirm"):
        return 0
    steps.start("backup", 2)
    proc = manage(["backup"], "Backing up before the update", "DT-U01", timeout=3600, check=False)
    if proc.returncode != 0:
        raise Failed("DT-U01", "The backup before the update failed, so the update was not started.",
                     ui.redact(proc.stdout[-400:]), "Fix backups first (Backups page), then update.")
    state_file = DATA / "status" / "backup-state.json"
    bstate = json.loads(state_file.read_text()) if state_file.exists() else {}
    pre = next(((d, s.get("last_file")) for d, s in bstate.get("destinations", {}).items() if s.get("last_file")), None)
    steps.ok(f"Backup made: {pre[1] if pre else 'yes'}")
    steps.start("files", 3)
    stop_service()
    copy_program(source)
    install_packages("DT-U02")
    steps.ok()
    try:
        steps.start("database", 1)
        manage(["migrate", "--noinput"], "Updating the database", "DT-U03")
        manage(["collectstatic", "--noinput"], "Preparing the screens", "DT-U03")
        manage(["setup_initial"], "Checking the starting data", "DT-U03")
        steps.ok()
        steps.start("start", 2)
        start_service(port)
        steps.ok(f"DiagTrack {new_version} is running.")
    except Failed as f:
        ui.say(ui.red(f"The update failed: {f.what}. Putting the previous version back..."))
        stop_service()
        failed = PROGRAM / "app.failed"
        shutil.rmtree(failed, ignore_errors=True)
        (PROGRAM / "app").rename(failed)
        (PROGRAM / "app.previous").rename(PROGRAM / "app")
        install_packages("DT-U04")
        if pre:
            manage(["restore", "--dest", pre[0], "--name", pre[1], "--yes"], "Restoring the backup made before the update",
                   "DT-U04", timeout=6 * 3600)
        start_service(port)
        raise Failed(f.code, f"The update failed and the previous version is back: {f.what}", f.cause,
                     "Send the log file to whoever supports DiagTrack.")
    st.update(version=new_version, updated_at=time.strftime("%Y-%m-%d %H:%M"))
    save_state(st)
    return 0


def repair(source):
    steps = ui.Steps({"files": "Checking the program", "secrets": "Checking passwords",
                      "service": "Service, permissions, firewall and safety net", "start": "Starting and checking"})
    st = load_state()
    pg = st.get("pg") or find_postgres()
    if not pg:
        raise Failed("DT-R01", "PostgreSQL 17 is not installed here.", todo="Choose a new install instead.")
    steps.start("files")
    if not (PROGRAM / "app" / "manage.py").exists():
        copy_program(source)
    install_packages("DT-R02")
    steps.ok()
    steps.start("secrets")
    secrets_ = load_secrets()
    if not secrets_.get("BACKUP_PASSPHRASE"):
        secrets_["BACKUP_PASSPHRASE"] = ui.ask_secret("The backup passphrase is missing. Type it (from the recovery sheet)",
                                                      check=common.passphrase_problem, key="backup_passphrase")
        save_secrets(secrets_)
    steps.ok()
    steps.start("service")
    stop_service()
    install_service(pg)
    set_permissions()
    firewall(st.get("port", 80))
    safety_task()
    command_line()
    steps.ok()
    steps.start("start")
    manage(["migrate", "--noinput"], "Checking the database", "DT-R03")
    manage(["collectstatic", "--noinput"], "Preparing the screens", "DT-R03")
    start_service(st.get("port", 80))
    steps.ok("DiagTrack is running.")
    return 0


def remove():
    ui.section("Remove DiagTrack")
    ui.say("This removes the DiagTrack service, its firewall rule, its scheduled task and the program folder.")
    if not ui.yes("Continue?", False, key="confirm"):
        return 0
    stop_service()
    if service_state(SERVICE) is not None:
        run([PROGRAM / "DiagTrackService.exe", "uninstall"], "Removing the service", "DT-X01", check=False)
    powershell(f"Unregister-ScheduledTask -TaskName '{TASK}' -Confirm:$false", "Removing the task", "DT-X01", check=False)
    run(["netsh", "advfirewall", "firewall", "delete", "rule", f"name={FIREWALL_RULE}"], "Firewall", "DT-X01", check=False)
    desktop = Path(os.environ.get("PUBLIC", r"C:\Users\Public")) / "Desktop"
    for name in ("Open DiagTrack.url", "DiagTrack Backups.url"):
        (desktop / name).unlink(missing_ok=True)
    ui.say("")
    ui.say(ui.amber("The data (database, attachments, settings) is still on this computer."))
    if ui.ask("To delete the data too, type DELETE. Anything else keeps it", "", key="delete_data") == "DELETE":
        st = load_state()
        pg = st.get("pg")
        secrets_ = load_secrets()
        if pg and secrets_.get("PG_SUPERPASSWORD"):
            try:
                with pg_connect(pg, secrets_["PG_SUPERPASSWORD"]) as conn:
                    for suffix in ("", "_previous", "_incoming", "_verify"):
                        conn.execute(f'DROP DATABASE IF EXISTS "diagtrack{suffix}"')
                    conn.execute("DROP ROLE IF EXISTS diagtrack")
            except Exception as exc:
                ui.say(ui.amber(f"The database could not be removed: {exc}"))
        shutil.rmtree(DATA, ignore_errors=True)
        ui.say("Data deleted. Backups in the backup folders were not touched.")
    shutil.rmtree(PROGRAM, ignore_errors=True)
    ui.say("DiagTrack is removed. PostgreSQL 17 stays installed; remove it in Settings, Apps if no longer needed.")
    if PROGRAM.exists():
        ui.say(f"Close this window, then delete {PROGRAM} by hand: setup's own Python was still in use.")
    return 0
