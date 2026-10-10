"""Import from Excel: download the workbook, upload it, see what will change, then import (administrators only)."""
import re
import secrets
import time

from django.contrib import messages
from django.http import HttpResponse
from django.shortcuts import redirect, render
from django.utils import timezone

from . import bulk_import, state
from .views import admin_only

MAX_BYTES = 5 * 1024 * 1024
KEEP_SECONDS = 24 * 3600


def _folder():
    folder = state.status_dir() / "imports"
    folder.mkdir(parents=True, exist_ok=True)
    for old in folder.glob("*.xlsx"):  # uploads nobody imported
        if time.time() - old.stat().st_mtime > KEEP_SECONDS:
            old.unlink(missing_ok=True)
    return folder


def _upload_path(token):
    if not re.fullmatch(r"[A-Za-z0-9_-]{20,64}", token or ""):
        return None
    path = _folder() / f"{token}.xlsx"
    return path if path.exists() else None


@admin_only
def import_page(request):
    if request.method == "POST" and request.POST.get("step") == "check":
        upload = request.FILES.get("file")
        if not upload:
            messages.error(request, "Choose the Excel file first.")
            return redirect("import_page")
        if upload.size > MAX_BYTES:
            messages.error(request, "That file is larger than 5 MB. An import workbook is normally far smaller.")
            return redirect("import_page")
        data = upload.read()
        try:
            plan = bulk_import.read(data)
        except bulk_import.ImportFileError as exc:
            messages.error(request, str(exc))
            return redirect("import_page")
        token = secrets.token_urlsafe(24)
        (_folder() / f"{token}.xlsx").write_bytes(data)
        return render(request, "import.html", {"plan": plan, "token": token, "file_name": upload.name,
                                               "sheets": _sheets(plan)})

    if request.method == "POST" and request.POST.get("step") == "import":
        path = _upload_path(request.POST.get("token"))
        if not path:
            messages.error(request, "That upload has expired. Upload the file again.")
            return redirect("import_page")
        plan = bulk_import.read(path.read_bytes())  # checked again: DiagTrack may have changed meanwhile
        if plan.errors:
            return render(request, "import.html", {"plan": plan, "token": path.stem, "file_name": "the file",
                                                   "sheets": _sheets(plan)})
        counts, passwords = bulk_import.apply(plan)
        path.unlink(missing_ok=True)
        return render(request, "import.html", {"done": True, "sheets": _sheets(plan), "passwords": passwords})

    return render(request, "import.html", {})


@admin_only
def import_workbook(request):
    response = HttpResponse(bulk_import.workbook(),
                            content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    response["Content-Disposition"] = f'attachment; filename="diagtrack-import-{timezone.localdate():%Y-%m-%d}.xlsx"'
    return response


def _sheets(plan):
    out = []
    for name in bulk_import.ORDER:
        rows = plan.rows[name]
        out.append({"name": name, "counts": plan.counts(name), "rows": rows,
                    "shown": [r for r in rows if r.action in ("error", "new", "change") or r.notes][:300],
                    "missing": name in plan.missing_sheets})
    return out
