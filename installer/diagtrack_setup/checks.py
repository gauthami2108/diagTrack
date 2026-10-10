"""Checks before anything is installed: system, space, memory, ports, internet."""
import ctypes
import os
import platform
import shutil
import socket
import sys
import urllib.request

from .ui import Failed

GB = 1024 ** 3


def windows_info():
    """(build, is_server, name)"""
    v = sys.getwindowsversion()
    is_server = getattr(v, "product_type", 1) != 1
    build = v.build
    if is_server:
        name = ("Windows Server 2025" if build >= 26100 else "Windows Server 2022" if build >= 20348 else
                "Windows Server 2019" if build >= 17763 else "Windows Server 2016" if build >= 14393 else
                f"Windows Server (build {build})")
    else:
        name = f"Windows 11 (build {build})" if build >= 22000 else f"Windows 10 (build {build})"
    return build, is_server, name


def check_windows():
    build, is_server, name = windows_info()
    if platform.machine().upper() not in ("AMD64", "X86_64"):
        raise Failed("DT-P01", f"This computer's processor ({platform.machine()}) is not supported.",
                     "Setup supports 64-bit Intel and AMD processors.", "Use a 64-bit x64 server.")
    if is_server and build < 14393:
        raise Failed("DT-P02", f"{name} is too old.", "Windows Server 2016 is the oldest supported version.",
                     "Use Windows Server 2016 or newer.")
    if not is_server and build < 22000:
        raise Failed("DT-P03", f"{name} is not supported.", "Microsoft ended support for Windows 10 in October 2025.",
                     "Use Windows 11, or Windows Server 2016 or newer.")
    return name, build, is_server


def memory_gb():
    if os.name != "nt":
        try:
            return os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES") / GB
        except (ValueError, OSError):
            return 0

    class MemStatus(ctypes.Structure):
        _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
                    ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                    ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                    ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                    ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]
    m = MemStatus()
    m.dwLength = ctypes.sizeof(MemStatus)
    ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(m))
    return m.ullTotalPhys / GB


def free_gb(path):
    probe = path
    while not os.path.exists(probe):
        parent = os.path.dirname(probe)
        if parent == probe:
            break
        probe = parent
    try:
        return shutil.disk_usage(probe).free / GB
    except OSError:
        return 0


def port_free(port, host="0.0.0.0"):
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.bind((host, port))
        return True
    except OSError:
        return False
    finally:
        s.close()


def first_free_port(candidates):
    return next((p for p in candidates if port_free(p)), None)


def _ssl_context():
    import ssl
    try:  # pip's certificate bundle: newer than the roots on an older Windows Server
        from pip._vendor import certifi
        return ssl.create_default_context(cafile=certifi.where())
    except Exception:
        return ssl.create_default_context()


def reachable(url, timeout=12):
    try:
        req = urllib.request.Request(url, method="HEAD", headers={"User-Agent": "DiagTrack-setup"})
        with urllib.request.urlopen(req, timeout=timeout, context=_ssl_context()) as resp:
            return resp.status < 500
    except Exception as exc:  # report the reason to the caller
        code = getattr(exc, "code", None)
        return bool(code and code < 500)


def basics(data_dir, need_gb=20, need_mem=4):
    problems, notes = [], []
    gb = free_gb(str(data_dir))
    if gb < need_gb:
        problems.append(f"Only {gb:.0f} GB free where DiagTrack keeps its data; {need_gb} GB is the minimum.")
    else:
        notes.append(f"{gb:.0f} GB free disk")
    mem = memory_gb()
    if mem and mem < need_mem - 0.3:
        problems.append(f"{mem:.1f} GB memory; {need_mem} GB is the minimum.")
    elif mem:
        notes.append(f"{mem:.1f} GB memory")
    return problems, notes
