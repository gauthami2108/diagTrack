"""
Where DiagTrack finds its settings and secrets.

Nothing here needs editing. The order is:

1. Real environment variables always win.
2. Native Windows install: DIAGTRACK_HOME (for example C:\\ProgramData\\DiagTrack) holds
   - diagtrack.conf   plain settings, one KEY=value per line (no secrets)
   - secrets.dat      the secrets, encrypted with Windows DPAPI for this computer
3. Docker: each secret is a file in /run/secrets (the project's "secrets" folder, mounted read-only),
   for example /run/secrets/db_password.

Secrets are never written to logs or passed on a command line.
"""
import json
import os
import sys
from pathlib import Path

SECRET_NAMES = ("SECRET_KEY", "DB_PASSWORD", "FIELD_KEY", "BACKUP_PASSPHRASE", "ADMIN_PASSWORD")
SECRETS_DIR = Path(os.environ.get("DIAGTRACK_SECRETS_DIR", "/run/secrets"))


def home():
    value = os.environ.get("DIAGTRACK_HOME", "")
    return Path(value) if value else None


def read_conf(path):
    values = {}
    try:
        text = Path(path).read_text(encoding="utf-8")
    except OSError:
        return values
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip()
    return values


def write_conf(path, values, header=""):
    lines = [f"# {line}" for line in header.splitlines()] if header else []
    lines += [f"{k}={v}" for k, v in values.items()]
    Path(path).write_text("\n".join(lines) + "\n", encoding="utf-8")


# --------------------------------------------------------------------------
# Windows DPAPI, machine scope: any process on this computer can decrypt, no other computer can.
# --------------------------------------------------------------------------
def _dpapi(data, protect):
    import ctypes
    from ctypes import wintypes

    class Blob(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]

    crypt32 = ctypes.windll.crypt32
    kernel32 = ctypes.windll.kernel32
    pblob = ctypes.POINTER(Blob)
    crypt32.CryptProtectData.argtypes = [pblob, wintypes.LPCWSTR, pblob, ctypes.c_void_p, ctypes.c_void_p,
                                         wintypes.DWORD, pblob]
    crypt32.CryptUnprotectData.argtypes = [pblob, ctypes.c_void_p, pblob, ctypes.c_void_p, ctypes.c_void_p,
                                           wintypes.DWORD, pblob]
    crypt32.CryptProtectData.restype = crypt32.CryptUnprotectData.restype = wintypes.BOOL
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    buf = ctypes.create_string_buffer(data, len(data))
    blob_in = Blob(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char)))
    blob_out = Blob()
    CRYPTPROTECT_UI_FORBIDDEN, CRYPTPROTECT_LOCAL_MACHINE = 0x1, 0x4
    flags = CRYPTPROTECT_UI_FORBIDDEN | CRYPTPROTECT_LOCAL_MACHINE
    if protect:
        ok = crypt32.CryptProtectData(ctypes.byref(blob_in), "DiagTrack", None, None, None, flags,
                                      ctypes.byref(blob_out))
    else:
        ok = crypt32.CryptUnprotectData(ctypes.byref(blob_in), None, None, None, None, flags,
                                        ctypes.byref(blob_out))
    if not ok:
        raise OSError(f"Windows could not {'protect' if protect else 'read'} the DiagTrack secrets "
                      f"(error {ctypes.GetLastError()}).")
    try:
        return ctypes.string_at(blob_out.pbData, blob_out.cbData)
    finally:
        kernel32.LocalFree(ctypes.cast(blob_out.pbData, ctypes.c_void_p))


def save_secrets(path, secrets):
    """Write the secrets file. Encrypted with DPAPI on Windows; elsewhere readable by the owner only."""
    raw = json.dumps(secrets).encode("utf-8")
    path = Path(path)
    if sys.platform == "win32":
        path.write_bytes(b"DPAPI1" + _dpapi(raw, True))
    else:
        path.write_bytes(raw)
        os.chmod(path, 0o600)


def load_secrets_file(path):
    try:
        raw = Path(path).read_bytes()
    except OSError:
        return {}
    if raw.startswith(b"DPAPI1"):
        raw = _dpapi(raw[6:], False)
    return json.loads(raw.decode("utf-8"))


def load():
    """Fill os.environ from the settings file, the secrets file and /run/secrets. Real variables win."""
    h = home()
    if h:
        for key, value in read_conf(h / "diagtrack.conf").items():
            os.environ.setdefault(key, value)
        for key, value in load_secrets_file(h / "secrets.dat").items():
            if value:
                os.environ.setdefault(key, value)
        os.environ.setdefault("MEDIA_ROOT", str(h / "media"))
        os.environ.setdefault("STATUS_DIR", str(h / "status"))
    for name in SECRET_NAMES:
        if os.environ.get(name):
            continue
        file_hint = os.environ.get(f"{name}_FILE")
        candidate = Path(file_hint) if file_hint else SECRETS_DIR / name.lower()
        try:
            value = candidate.read_text(encoding="utf-8").strip()
        except OSError:
            continue
        if value:
            os.environ[name] = value
