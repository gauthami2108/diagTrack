"""Pieces shared by the Windows and Docker setups."""
import base64
import os
import secrets
import sys
import tempfile
from datetime import datetime
from pathlib import Path

from . import ui


def app_dir(source):
    """The application folder inside the release, so setup can use its encryption and settings code."""
    path = Path(source) / "app"
    if not (path / "manage.py").exists():
        path = Path("/app")  # inside the Docker image
    if not (path / "manage.py").exists():
        path = Path(__file__).resolve().parent.parent.parent / "app"  # running from the release folder
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))
    return path


def new_password(n=32):
    return secrets.token_urlsafe(n)[:n]


def new_key():
    return base64.urlsafe_b64encode(os.urandom(32)).decode("ascii").rstrip("=")


def password_hash(password):
    """Django's one-way hash of the administrator password, so the password itself is never stored."""
    from django.conf import settings
    if not settings.configured:
        settings.configure(PASSWORD_HASHERS=["django.contrib.auth.hashers.PBKDF2PasswordHasher"], USE_TZ=True)
    from django.contrib.auth.hashers import make_password
    return make_password(password)


def passphrase_problem(pp):
    if len(pp) < 12:
        return "Use at least 12 characters. A short sentence works well, for example: blue lamps over Kukatpally."
    if len(set(pp)) < 5:
        return "That passphrase is too easy to guess."
    return None


def read_move_file(path, passphrase):
    """Open a backup or move file with its passphrase. Returns its readable summary, or raises ui.Failed."""
    from tickets.backup import container
    try:
        with open(path, "rb") as f:
            reader = container.Reader(f, passphrase)
            reader.read(512)
            return reader.summary
    except container.BadPassphrase:
        raise ui.Failed("DT-M01", "The passphrase does not open this file.",
                        "It must be the backup passphrase of the server the file came from.",
                        "Check the recovery sheet of the old server and try again.") from None
    except (container.Damaged, OSError) as exc:
        raise ui.Failed("DT-M02", f"The file cannot be read: {exc}", "The copy may be incomplete or damaged.",
                        "Copy the file from the old server again and compare its checksum.") from None


def describe_summary(s):
    c = s.get("counts") or {}
    a = s.get("attachments") or {}
    return (f"made {str(s.get('created', '?'))[:16].replace('T', ' ')} UTC by DiagTrack {s.get('app_version', '?')} "
            f"on {s.get('platform', '?')}: {c.get('tickets', '?')} tickets, {c.get('users', '?')} users, "
            f"{a.get('count', '?')} attachment files")


def recovery_sheet(values):
    lines = [
        "DIAGTRACK RECOVERY SHEET",
        "Keep this page in a locked drawer or safe. Two named people should know where it is.",
        "",
        f"Printed:             {datetime.now():%d %b %Y %H:%M}",
    ]
    lines += [f"{k + ':':<21}{v}" for k, v in values.items()]
    lines += [
        "",
        "Without the backup passphrase, encrypted backups cannot be opened by anyone.",
        "The database password is needed only for repairs; setup keeps a protected copy on the server.",
    ]
    return "\n".join(lines) + "\n"


def offer_recovery_sheet(values):
    text = recovery_sheet(values)
    ui.section("Recovery sheet")
    if ui.ANSWERS:
        ui.say("Unattended run: the recovery sheet is not shown.")
        return
    ui.say("The next screen shows the backup passphrase and database password ONCE.")
    if os.name == "nt" and ui.yes("Print it now on the default printer?", True):
        tmp = Path(tempfile.gettempdir()) / f"diagtrack-recovery-{secrets.token_hex(4)}.txt"
        tmp.write_text(text, encoding="utf-8")
        try:
            os.system(f'notepad.exe /p "{tmp}"')
        finally:
            tmp.unlink(missing_ok=True)
        ui.say("Sent to the printer.")
    if ui.yes("Show it on screen to copy by hand?", not os.name == "nt"):
        print("\n" + text)
        input("Press Enter once it is written down or printed. It is not shown again.")
        print("\n" * 40)
