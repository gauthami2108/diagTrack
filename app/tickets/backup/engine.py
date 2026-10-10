"""
Backups: make, check, restore, test-restore, retention.

A backup folder (destination) looks like this:

    store.json                         salt and passphrase check for this folder
    backups/diagtrack-<site>-<when>-<kind>.dtbak
                                       one encrypted file per backup: manifest, keys, attachment list,
                                       database copy (pg_dump custom format)
    attachments/ab/ab12...ef.dta       each attachment stored once, encrypted, named by its SHA-256

A full copy (for a USB drive, an off-site copy or a move) is a single .dtbak that also carries the
attachments inside it.
"""
import hashlib
import io
import json
import logging
import os
import platform
import random
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
from datetime import datetime, timezone as dt_timezone
from pathlib import Path, PurePosixPath

from django.conf import settings
from django.db import connection, connections
from django.utils import timezone

from .. import crypto, state
from . import container
from .container import BadPassphrase, Damaged

log = logging.getLogger("diagtrack.backup")
COUNT_TABLES = {
    "tickets": "tickets_ticket", "comments": "tickets_ticketcomment", "attachments": "tickets_ticketattachment",
    "users": "tickets_user", "centres": "tickets_centre", "equipment": "tickets_equipment",
    "history": "tickets_tickethistory",
}
WINDOWS_BAD = re.compile(r'[<>:"|?*\x00-\x1f]')
WINDOWS_RESERVED = {"con", "prn", "aux", "nul", *(f"com{i}" for i in range(1, 10)), *(f"lpt{i}" for i in range(1, 10))}


class BackupError(Exception):
    pass


def passphrase():
    value = os.environ.get("BACKUP_PASSPHRASE", "")
    if not value:
        raise BackupError("No backup passphrase is set. Backups are always encrypted, so run setup and choose "
                          "Repair to set one.")
    return value


def _noop(percent, step):
    pass


# --------------------------------------------------------------------------
# PostgreSQL tools
# --------------------------------------------------------------------------
def db():
    d = settings.DATABASES["default"]
    if "postgresql" not in d["ENGINE"]:
        raise BackupError("Backups need the PostgreSQL database.")
    return {"host": d.get("HOST") or "localhost", "port": str(d.get("PORT") or 5432), "user": d["USER"],
            "password": d.get("PASSWORD", ""), "name": d["NAME"]}


def tool(name):
    exe = f"{name}.exe" if sys.platform == "win32" else name
    if settings.PG_BIN:
        path = Path(settings.PG_BIN) / exe
        if path.exists():
            return str(path)
    found = shutil.which(name)
    if not found:
        raise BackupError(f"{name} was not found. Set PG_BIN to the PostgreSQL bin folder.")
    return found


def run_tool(args, what):
    d = db()
    env = dict(os.environ, PGPASSWORD=d["password"], PGCONNECT_TIMEOUT="15")
    proc = subprocess.run(args, env=env, capture_output=True, text=True,
                          creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    if proc.returncode != 0:
        detail = "\n".join((proc.stderr or proc.stdout or "").strip().splitlines()[-4:])
        raise BackupError(f"{what} failed: {detail or 'no details given'}")
    return proc.stdout


def admin_connection(dbname="postgres"):
    import psycopg
    d = db()
    return psycopg.connect(host=d["host"], port=d["port"], user=d["user"], password=d["password"],
                           dbname=dbname, autocommit=True, connect_timeout=15)


def server_version():
    with connection.cursor() as cur:
        cur.execute("SHOW server_version")
        return cur.fetchone()[0]


def tool_version(name):
    try:
        return run_tool([tool(name), "--version"], name).strip().split()[-1]
    except BackupError:
        return ""


# --------------------------------------------------------------------------
# What is in the database
# --------------------------------------------------------------------------
def counts(conn=None):
    out = {}
    if conn is None:
        with connection.cursor() as cur:
            for key, table in COUNT_TABLES.items():
                cur.execute(f'SELECT count(*) FROM "{table}"')
                out[key] = cur.fetchone()[0]
        return out
    with conn.cursor() as cur:
        for key, table in COUNT_TABLES.items():
            cur.execute(f'SELECT count(*) FROM "{table}"')
            out[key] = cur.fetchone()[0]
    return out


def applied_migrations():
    from django.db.migrations.recorder import MigrationRecorder
    return sorted([a, n] for a, n in MigrationRecorder(connection).applied_migrations())


def known_migrations():
    from django.db.migrations.loader import MigrationLoader
    return {(a, n) for a, n in MigrationLoader(None, ignore_no_migrations=True).disk_migrations}


def site_slug():
    from ..models import SiteSettings
    name = SiteSettings.load().org_name or "diagtrack"
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:30] or "diagtrack"


# --------------------------------------------------------------------------
# Attachments on this server
# --------------------------------------------------------------------------
def sha256_file(path):
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def media_files(progress=_noop):
    """Every file under MEDIA_ROOT with its checksum. Checksums are cached by size and time."""
    root = Path(settings.MEDIA_ROOT)
    cache_path = state.status_dir() / "media-index.json"
    cache = state.read_json(cache_path, {}) or {}
    out, fresh = [], {}
    if not root.exists():
        return out
    paths = [p for p in root.rglob("*") if p.is_file() and not p.name.endswith((".part", ".tmp"))]
    for i, p in enumerate(paths):
        rel = p.relative_to(root).as_posix()
        st = p.stat()
        hit = cache.get(rel)
        if hit and hit["size"] == st.st_size and hit["mtime"] == int(st.st_mtime):
            sha = hit["sha256"]
        else:
            sha = sha256_file(p)
        fresh[rel] = {"size": st.st_size, "mtime": int(st.st_mtime), "sha256": sha}
        out.append({"path": rel, "size": st.st_size, "sha256": sha})
        if i % 200 == 0:
            progress(None, f"Checking attachments: {i} of {len(paths)}")
    state.write_json(cache_path, fresh)
    return out


# --------------------------------------------------------------------------
# A backup folder's store
# --------------------------------------------------------------------------
def store_salt(root, create=True):
    """Each folder has one salt, so the key is worked out once per folder, and a check that the
    passphrase matches the one the folder was started with."""
    path = Path(root) / "store.json"
    data = state.read_json(path)
    pp = passphrase()
    if data:
        salt = container._b64d(data["salt"])
        check = io.BytesIO(container._b64d(data["check"]))
        try:
            container.Reader(check, pp).read()
        except BadPassphrase as exc:
            raise BackupError("This backup folder was started with a different backup passphrase. Use the "
                              "passphrase from the recovery sheet, or choose an empty folder.") from exc
        return salt
    if not create:
        raise BackupError(f"{root} is not a DiagTrack backup folder.")
    salt = container.new_salt()
    buf = io.BytesIO()
    w = container.Writer(buf, pp, salt, {"kind": "check"})
    w.write(b"diagtrack")
    w.close()
    state.write_json(path, {"format": 1, "salt": container._b64e(salt), "check": container._b64e(buf.getvalue()),
                            "created": state.now_iso()})
    return salt


def blob_path(root, sha):
    return Path(root) / "attachments" / sha[:2] / f"{sha}.dta"


# --------------------------------------------------------------------------
# Making a backup
# --------------------------------------------------------------------------
def _add_bytes(tar, name, data):
    info = tarfile.TarInfo(name)
    info.size = len(data)
    info.mtime = int(datetime.now().timestamp())
    tar.addfile(info, io.BytesIO(data))


def _work_dir():
    path = state.status_dir() / "tmp"
    path.mkdir(parents=True, exist_ok=True)
    return Path(tempfile.mkdtemp(prefix="work-", dir=path))


def dump_database(target):
    d = db()
    run_tool([tool("pg_dump"), "-h", d["host"], "-p", d["port"], "-U", d["user"], "-d", d["name"], "-Fc",
              "--no-owner", "--no-privileges", "-f", str(target)], "The database copy")
    run_tool([tool("pg_restore"), "--list", str(target)], "Checking the database copy")


def create(root=None, kind="scheduled", full_to=None, progress=_noop, extra=None):
    """Make a backup into destination folder `root`, or a full copy into folder `full_to`.
    Returns a dict describing the file."""
    pp = passphrase()
    if full_to:
        out_dir = Path(full_to)
        out_dir.mkdir(parents=True, exist_ok=True)
        salt = container.new_salt()
    else:
        out_dir = Path(root) / "backups"
        salt = store_salt(root)
    work = _work_dir()
    try:
        progress(5, "Copying the database")
        dump = work / "database.dump"
        dump_database(dump)
        dump_sha, dump_size = sha256_file(dump), dump.stat().st_size

        progress(30, "Checking attachments")
        files = media_files(progress)
        if not full_to:
            new = [f for f in files if not blob_path(root, f["sha256"]).exists()]
            for i, f in enumerate(new):
                target = blob_path(root, f["sha256"])
                target.parent.mkdir(parents=True, exist_ok=True)
                container.encrypt_file(Path(settings.MEDIA_ROOT) / f["path"], target, pp, salt,
                                       {"kind": "attachment"})
                if i % 20 == 0:
                    progress(30 + int(40 * i / max(1, len(new))), f"Copying new attachments: {i + 1} of {len(new)}")

        progress(72, "Writing the backup file")
        created = datetime.now(dt_timezone.utc)
        cnt = counts()
        manifest = {
            "format": 1, "created": created.isoformat(timespec="seconds"), "kind": kind, "full": bool(full_to),
            "site": site_slug(), "app_version": settings.DIAGTRACK_VERSION, "schema": applied_migrations(),
            "pg_server": server_version(), "pg_dump": tool_version("pg_dump"),
            "platform": f"{platform.system()} {platform.release()}", "install": os.environ.get("DIAGTRACK_INSTALL", ""),
            "time_zone": settings.TIME_ZONE, "counts": cnt,
            "files": {"database.dump": {"sha256": dump_sha, "size": dump_size}},
            "attachments": {"count": len(files), "bytes": sum(f["size"] for f in files)},
            "clashes": name_clashes(files),
        }
        if extra:
            manifest.update(extra)
        summary = {k: manifest[k] for k in ("created", "kind", "full", "site", "app_version", "pg_server",
                                             "platform", "install", "counts", "attachments")}
        summary["latest_migration"] = manifest["schema"][-1] if manifest["schema"] else None
        summary["db_size"] = dump_size
        stamp = timezone.localtime(created).strftime("%Y%m%d-%H%M%S")
        name = f"diagtrack-{manifest['site']}-{stamp}-{kind}.dtbak"
        out_dir.mkdir(parents=True, exist_ok=True)
        final = out_dir / name
        part = out_dir / f"{name}.part"
        with open(part, "wb") as fout:
            w = container.Writer(fout, pp, salt, summary)
            with tarfile.open(fileobj=w, mode="w|", format=tarfile.PAX_FORMAT) as tar:
                _add_bytes(tar, "manifest.json", json.dumps(manifest, indent=1).encode("utf-8"))
                _add_bytes(tar, "keys.json", json.dumps({"field_key": crypto.field_key_text()}).encode("utf-8"))
                _add_bytes(tar, "attachments.json", json.dumps(files).encode("utf-8"))
                tar.add(str(dump), arcname="database.dump")
                if full_to:
                    seen = set()
                    for i, f in enumerate(files):
                        if f["sha256"] in seen:
                            continue
                        seen.add(f["sha256"])
                        tar.add(str(Path(settings.MEDIA_ROOT) / f["path"]), arcname=f"attachments/{f['sha256']}")
                        if i % 20 == 0:
                            progress(72 + int(18 * i / max(1, len(files))), f"Adding attachments: {i + 1} of {len(files)}")
            w.close()
            fout.flush()
            os.fsync(fout.fileno())
        os.replace(part, final)

        progress(90, "Checking the backup")
        check = verify(final, None if full_to else root, sample=10)
        progress(100, "Backup completed")
        return {"file": str(final), "name": name, "size": final.stat().st_size, "created": manifest["created"],
                "counts": cnt, "attachments": len(files), "sha256": sha256_file(final), "check": check}
    finally:
        shutil.rmtree(work, ignore_errors=True)


# --------------------------------------------------------------------------
# Reading a backup
# --------------------------------------------------------------------------
def open_backup(path, pp=None):
    f = open(path, "rb")
    try:
        reader = container.Reader(f, pp or passphrase())
    except Exception:
        f.close()
        raise
    return f, reader


def read_meta(path, pp=None):
    """manifest, keys and attachment list, without reading the whole file."""
    f, reader = open_backup(path, pp)
    out = {}
    try:
        with tarfile.open(fileobj=reader, mode="r|") as tar:
            for member in tar:
                if member.name in ("manifest.json", "keys.json", "attachments.json"):
                    out[member.name] = json.loads(tar.extractfile(member).read().decode("utf-8"))
                if len(out) == 3 or member.name == "database.dump":
                    break
    finally:
        f.close()
    return out.get("manifest", {}), out.get("keys", {}), out.get("attachments.json", [])


def verify(path, root=None, sample=20, progress=_noop):
    """Read the whole file, check every part against its checksum, and check that the attachments it
    needs are in the folder (decrypting a sample of them)."""
    f, reader = open_backup(path)
    manifest, files, seen_dump, full_shas = {}, [], False, set()
    try:
        with tarfile.open(fileobj=reader, mode="r|") as tar:
            for member in tar:
                data = tar.extractfile(member)
                if member.name == "manifest.json":
                    manifest = json.loads(data.read().decode("utf-8"))
                elif member.name == "attachments.json":
                    files = json.loads(data.read().decode("utf-8"))
                elif member.name == "database.dump":
                    digest = hashlib.sha256()
                    for block in iter(lambda: data.read(1024 * 1024), b""):
                        digest.update(block)
                    if digest.hexdigest() != manifest.get("files", {}).get("database.dump", {}).get("sha256"):
                        raise Damaged("The database copy inside the backup does not match its checksum.")
                    seen_dump = True
                elif member.name.startswith("attachments/"):
                    digest = hashlib.sha256()
                    for block in iter(lambda: data.read(1024 * 1024), b""):
                        digest.update(block)
                    if digest.hexdigest() != member.name.split("/", 1)[1]:
                        raise Damaged(f"An attachment inside the backup is damaged ({member.name}).")
                    full_shas.add(member.name.split("/", 1)[1])
        reader.finish()
    finally:
        f.close()
    if not seen_dump:
        raise Damaged("The backup has no database copy in it.")
    needed = {x["sha256"] for x in files}
    missing = []
    if manifest.get("full"):
        missing = sorted(needed - full_shas)
    elif root is not None:
        missing = [s for s in needed if not blob_path(root, s).exists()]
        pool = [s for s in needed if s not in missing]
        work = _work_dir()
        try:
            for i, sha in enumerate(random.sample(pool, min(sample, len(pool)))):
                out = work / "probe"
                if container.decrypt_file(blob_path(root, sha), out, passphrase()) != sha:
                    raise Damaged(f"A stored attachment is damaged ({sha[:12]}).")
                out.unlink()
        finally:
            shutil.rmtree(work, ignore_errors=True)
    if missing:
        raise Damaged(f"{len(missing)} attachment(s) needed by this backup are missing from the folder.")
    return {"ok": True, "checked": state.now_iso(), "attachments": len(needed)}


# --------------------------------------------------------------------------
# Listing and retention
# --------------------------------------------------------------------------
def catalog(root):
    out = []
    folder = Path(root) / "backups"
    if not folder.exists():
        return out
    for p in folder.glob("*.dtbak"):
        try:
            summary = container.summary_of(p)
        except (OSError, Damaged):
            continue
        created = state.parse_iso(summary.get("created")) or datetime.fromtimestamp(p.stat().st_mtime, dt_timezone.utc)
        out.append({"name": p.name, "path": str(p), "summary": summary, "created": created, "size": p.stat().st_size})
    out.sort(key=lambda e: e["created"], reverse=True)
    return out


def apply_retention(root, dest):
    """Remove backups the retention rules no longer keep, then attachments no kept backup needs."""
    from datetime import timedelta

    from .destinations import to_keep
    everything = catalog(root)
    scheduled = [e for e in everything if e["summary"].get("kind") == "scheduled"]
    keep = to_keep(scheduled, dest)
    if everything:
        keep.add(everything[0]["name"])  # the newest backup of any kind always stays
    manual_limit = timezone.now() - timedelta(days=int(dest.get("keep_manual_days", 30) or 0))
    removed = []
    for e in everything:
        kind = e["summary"].get("kind")
        drop = (kind == "scheduled" and e["name"] not in keep) or \
               (kind == "manual" and e["created"] < manual_limit and e["name"] not in keep)
        if drop:
            Path(e["path"]).unlink(missing_ok=True)
            removed.append(e["name"])
    # Attachments: keep everything any remaining backup refers to. If one cannot be read, keep all.
    needed = set()
    for e in catalog(root):
        try:
            needed |= {x["sha256"] for x in read_meta(e["path"])[2]}
        except Exception:
            log.warning("Could not read %s; no attachments removed this time.", e["name"])
            return removed, 0
    dropped = 0
    for blob in (Path(root) / "attachments").glob("*/*.dta"):
        if blob.stem not in needed:
            blob.unlink(missing_ok=True)
            dropped += 1
    return removed, dropped


# --------------------------------------------------------------------------
# File names that would not survive another operating system
# --------------------------------------------------------------------------
def _windows_unsafe(rel):
    if len(str(Path(settings.MEDIA_ROOT) / rel)) > 240:
        return True
    for part in PurePosixPath(rel).parts:
        if WINDOWS_BAD.search(part) or part.split(".")[0].lower() in WINDOWS_RESERVED or part.endswith((" ", ".")):
            return True
    return False


def name_clashes(files):
    """Paths that differ only by letter case, or that Windows cannot store."""
    seen, out = {}, []
    for f in files:
        low = f["path"].lower()
        if low in seen and seen[low] != f["path"]:
            out.append({"path": f["path"], "problem": f"same name as {seen[low]} except for letter case"})
        seen.setdefault(low, f["path"])
        if any(WINDOWS_BAD.search(p) for p in PurePosixPath(f["path"]).parts):
            out.append({"path": f["path"], "problem": "contains characters Windows does not allow"})
    return out


def plan_targets(files):
    """Where each attachment goes on this computer. Renames what would clash or not fit."""
    planned, used, renames = [], set(), {}
    case_insensitive = sys.platform in ("win32", "darwin")
    for f in files:
        rel = f["path"]
        parts = PurePosixPath(rel).parts
        if not parts or rel.startswith("/") or ".." in parts:
            continue  # never write outside the attachments folder
        key = rel.lower() if case_insensitive else rel
        if key in used or (sys.platform == "win32" and _windows_unsafe(rel)):
            p = PurePosixPath(rel)
            safe_stem = WINDOWS_BAD.sub("_", p.stem)[:60]
            rel_new = f"restored/{safe_stem}-{f['sha256'][:10]}{p.suffix[:10]}"
            renames[rel] = rel_new
            rel = rel_new
            key = rel.lower() if case_insensitive else rel
        used.add(key)
        planned.append((f, rel))
    return planned, renames


def apply_renames(renames):
    """Point the database at renamed attachment files."""
    from ..models import SiteSettings, TicketAttachment, User
    for old, new in renames.items():
        TicketAttachment.objects.filter(file=old).update(file=new)
        User.objects.filter(photo=old).update(photo=new)
        SiteSettings.objects.filter(logo=old).update(logo=new)
        SiteSettings.objects.filter(logo_wide=old).update(logo_wide=new)


# --------------------------------------------------------------------------
# Restore
# --------------------------------------------------------------------------
def check_compatible(manifest):
    unknown = {tuple(x) for x in manifest.get("schema", [])} - known_migrations()
    if unknown:
        raise BackupError(f"This backup was made by a newer DiagTrack ({manifest.get('app_version', '?')}). "
                          f"Install version {manifest.get('app_version', '?')} or newer here first, then restore.")


def _extract(path, root, work, progress):
    """Decrypt the database copy into `work`; for a full copy also the attachments. Returns manifest, keys, files."""
    f, reader = open_backup(path)
    manifest, keys, files = {}, {}, []
    try:
        with tarfile.open(fileobj=reader, mode="r|") as tar:
            for member in tar:
                data = tar.extractfile(member)
                if member.name == "manifest.json":
                    manifest = json.loads(data.read().decode("utf-8"))
                    check_compatible(manifest)
                elif member.name == "keys.json":
                    keys = json.loads(data.read().decode("utf-8"))
                elif member.name == "attachments.json":
                    files = json.loads(data.read().decode("utf-8"))
                elif member.name == "database.dump":
                    progress(15, "Unpacking the database copy")
                    with open(work / "database.dump", "wb") as out:
                        shutil.copyfileobj(data, out, 1024 * 1024)
                elif member.name.startswith("attachments/"):
                    sha = member.name.split("/", 1)[1]
                    if re.fullmatch(r"[0-9a-f]{64}", sha):
                        (work / "attachments").mkdir(exist_ok=True)
                        with open(work / "attachments" / sha, "wb") as out:
                            shutil.copyfileobj(data, out, 1024 * 1024)
        reader.finish()
    finally:
        f.close()
    if sha256_file(work / "database.dump") != manifest["files"]["database.dump"]["sha256"]:
        raise Damaged("The database copy inside the backup does not match its checksum.")
    return manifest, keys, files


def _restore_into(dbname, dump):
    d = db()
    with admin_connection() as conn:
        conn.execute(f'DROP DATABASE IF EXISTS "{dbname}"')
        conn.execute(f'CREATE DATABASE "{dbname}"')
    run_tool([tool("pg_restore"), "-h", d["host"], "-p", d["port"], "-U", d["user"], "-d", dbname,
              "--no-owner", "--no-privileges", "--exit-on-error", str(dump)], "Loading the database copy")


def _swap(live, incoming, previous):
    """Put `incoming` in place of `live`, keeping the old one as `previous`."""
    import psycopg
    last = None
    for _ in range(10):
        try:
            with admin_connection() as conn:
                conn.execute(f'DROP DATABASE IF EXISTS "{previous}"')
                conn.execute("SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                             "WHERE datname = %s AND pid <> pg_backend_pid()", [live])
                conn.execute(f'ALTER DATABASE "{live}" RENAME TO "{previous}"')
                try:
                    conn.execute(f'ALTER DATABASE "{incoming}" RENAME TO "{live}"')
                except psycopg.Error:
                    conn.execute(f'ALTER DATABASE "{previous}" RENAME TO "{live}"')
                    raise
            return
        except psycopg.Error as exc:
            last = exc
            import time
            time.sleep(2)
    raise BackupError(f"Could not switch to the restored database: {last}")


def _migrate_subprocess(dbname):
    env = dict(os.environ, DB_NAME=dbname)
    manage = Path(settings.BASE_DIR) / "manage.py"
    proc = subprocess.run([sys.executable, str(manage), "migrate", "--noinput"], env=env, capture_output=True,
                          text=True, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    if proc.returncode != 0:
        detail = "\n".join((proc.stderr or proc.stdout).strip().splitlines()[-4:])
        raise BackupError(f"Bringing the restored database up to this version failed: {detail}")


def rekey_fields(old_key_text):
    """Secrets in the restored database were encrypted with the old server's key: re-encrypt them
    with this server's key."""
    old = crypto._b64d(old_key_text)
    if old == crypto.field_key():
        return 0
    changed = 0
    with connection.cursor() as cur:
        cur.execute("SELECT id, smtp_password, sms_headers, whatsapp_headers FROM tickets_sitesettings")
        for row_id, *values in cur.fetchall():
            fresh = [crypto.encrypt(crypto.decrypt(v, key=old)) if v else v for v in values]
            cur.execute("UPDATE tickets_sitesettings SET smtp_password = %s, sms_headers = %s, whatsapp_headers = %s "
                        "WHERE id = %s", [*fresh, row_id])
            changed += 1
    return changed


def restore(path, root=None, progress=_noop):
    """Restore a backup. The restored database is loaded and checked beside the live one, and only then
    switched in. The live database is kept as <name>_previous until the next restore."""
    live = db()["name"]
    incoming, previous = f"{live}_incoming", f"{live}_previous"
    work = _work_dir()
    try:
        progress(5, "Opening the backup")
        manifest, keys, files = _extract(path, root, work, progress)
        progress(30, "Loading the database copy beside the live one")
        _restore_into(incoming, work / "database.dump")
        progress(55, "Bringing it up to this version")
        _migrate_subprocess(incoming)
        progress(65, "Checking it against the backup's record")
        with admin_connection(incoming) as conn:
            got = counts(conn)
        expected = manifest.get("counts", {})
        wrong = {k: (expected[k], got.get(k)) for k in expected if got.get(k) != expected[k]}
        if wrong:
            raise BackupError("The restored data does not match the backup's record: " +
                              ", ".join(f"{k} {a} expected, {b} found" for k, (a, b) in wrong.items()))
        progress(75, "Switching to the restored database")
        connections.close_all()
        _swap(live, incoming, previous)
        connections.close_all()

        progress(80, "Restoring attachments")
        planned, renames = plan_targets(files)
        media = Path(settings.MEDIA_ROOT)
        restored = 0
        for i, (f, rel) in enumerate(planned):
            target = media / rel
            if target.exists() and target.stat().st_size == f["size"] and sha256_file(target) == f["sha256"]:
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            src_full = work / "attachments" / f["sha256"]
            if src_full.exists():
                shutil.copyfile(src_full, target)
            elif root is not None and blob_path(root, f["sha256"]).exists():
                container.decrypt_file(blob_path(root, f["sha256"]), target, passphrase())
            else:
                raise BackupError(f"The attachment {f['path']} is not in the backup folder.")
            restored += 1
            if i % 50 == 0:
                progress(80 + int(15 * i / max(1, len(planned))), f"Restoring attachments: {i + 1} of {len(planned)}")
        apply_renames(renames)
        if keys.get("field_key"):
            rekey_fields(keys["field_key"])
        progress(100, "Restore completed")
        return {"backup": Path(path).name, "created": manifest.get("created"), "counts": got,
                "attachments_restored": restored, "renamed": renames, "previous_database": previous,
                "from_version": manifest.get("app_version"), "from_platform": manifest.get("platform")}
    finally:
        shutil.rmtree(work, ignore_errors=True)
        try:
            with admin_connection() as conn:
                conn.execute(f'DROP DATABASE IF EXISTS "{incoming}"')
        except Exception:
            pass


def test_restore(path, root=None, progress=_noop):
    """Restore into a scratch database, compare with the backup's record, then remove it.
    Proves the backup can be restored without touching live data."""
    live = db()["name"]
    scratch = f"{live}_verify"
    work = _work_dir()
    try:
        progress(10, "Opening the backup")
        manifest, keys, files = _extract(path, root, work, progress)
        progress(40, "Loading it into a scratch database")
        _restore_into(scratch, work / "database.dump")
        progress(80, "Comparing with the backup's record")
        with admin_connection(scratch) as conn:
            got = counts(conn)
        expected = manifest.get("counts", {})
        wrong = {k: (expected[k], got.get(k)) for k in expected if got.get(k) != expected[k]}
        if wrong:
            raise BackupError("The test restore does not match the backup's record: " +
                              ", ".join(f"{k} {a} expected, {b} found" for k, (a, b) in wrong.items()))
        progress(100, "Test restore passed")
        return {"backup": Path(path).name, "counts": got, "attachments": len(files)}
    finally:
        shutil.rmtree(work, ignore_errors=True)
        try:
            with admin_connection() as conn:
                conn.execute(f'DROP DATABASE IF EXISTS "{scratch}"')
        except Exception:
            pass
