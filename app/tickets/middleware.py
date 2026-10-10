"""
Maintenance and read-only modes, set by the backup runner through files in the status folder.

Maintenance: during a restore the database is being replaced, so every page shows "Back shortly"
without touching the database, and refreshes itself when the restore is over.
Read-only: while moving to a new server, people can look but nothing can be changed.
"""
import time

from django.contrib import messages
from django.http import HttpResponse, JsonResponse
from django.shortcuts import redirect
from django.template.loader import render_to_string

from . import state

_cache = {"at": 0.0, "maint": None, "ro": None}
READONLY_ALLOWED = ("/login/", "/logout/", "/settings/backups/", "/password-reset/", "/reset/")


def current():
    """Re-read the flag files at most once a second."""
    now = time.monotonic()
    if now - _cache["at"] > 1.0:
        _cache.update(at=now, maint=state.maintenance(), ro=state.readonly())
    return _cache["maint"], _cache["ro"]


class MaintenanceMiddleware:
    """Placed early: answers without sessions or the database."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        maint, _ = current()
        path = request.path
        if maint and not path.startswith("/static/"):
            if path == "/maintenance/status.json":
                job = state.get_job(maint.get("job", ""))
                return JsonResponse({"maintenance": True, "step": job.get("step", "") if job else "",
                                     "percent": job.get("percent", 0) if job else 0})
            if path == "/healthz":
                return JsonResponse({"ok": False, "maintenance": True}, status=503)
            html = render_to_string("maintenance.html", {"reason": maint.get("reason", "")})
            resp = HttpResponse(html, status=503)
            resp["Retry-After"] = "30"
            return resp
        if path == "/maintenance/status.json":
            return JsonResponse({"maintenance": False})
        return self.get_response(request)


class ReadOnlyMiddleware:
    """Placed after sessions and messages, so it can explain why nothing changed."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        _, ro = current()
        path = request.path
        request.readonly = ro
        if ro and request.method == "POST" and not path.startswith(READONLY_ALLOWED):
            messages.error(request, "DiagTrack is read-only while it moves to a new server. Nothing was changed.")
            return redirect(request.META.get("HTTP_REFERER") or "/")
        return self.get_response(request)
