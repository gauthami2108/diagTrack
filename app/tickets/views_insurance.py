"""
Insurance register (Oversee > Insurance).

Policies per centre (one, several or all) and type of cover, with start and expiry dates, documents,
reminders (see services.insurance_checks), a renew action and an Excel export.

Who sees what:
  administrators and management        every policy; sum insured and premium (unless switched off in Settings)
  centre and area managers             policies of their own centres only, no money figures
  administrators only                  add, change, renew, delete, upload documents, manage cover types
"""
import io
from datetime import date, timedelta

from django import forms
from django.contrib import messages
from django.db.models import Count, Q
from django.http import FileResponse, Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from .models import (Centre, Equipment, InsuranceCoverType, InsuranceDocument, InsurancePolicy, SiteSettings, User)
from .services import insurance_stages
from .views import _safe, admin_only, role_required

MAX_FILE_MB = 20
# Allowed uploads: extension -> what the first bytes of a real file of that kind look like.
SIGNATURES = {"pdf": b"%PDF", "jpg": b"\xff\xd8\xff", "jpeg": b"\xff\xd8\xff", "png": b"\x89PNG", "webp": b"RIFF"}
STATUS_FILTERS = [("", "Current (not renewed or cancelled)"), ("active", "Active"), ("expiring", "Expiring soon"),
                  ("expired", "Expired"), ("renewed", "Renewed"), ("cancelled", "Cancelled"), ("all", "Everything")]

viewer = role_required(lambda u: u.can_view_insurance)
manager = role_required(lambda u: u.can_manage_insurance)


def _show_money(user, cfg=None):
    cfg = cfg or SiteSettings.load()
    return bool(user.insurance_money and cfg.insurance_show_money)


def _scoped(user):
    qs = InsurancePolicy.objects.select_related("cover_type").prefetch_related("centres", "responsible")
    return qs if user.insurance_sees_all else qs.filter(centres__in=user.centres.all()).distinct()


def _soon(policy, cfg):
    """Days before expiry at which a policy shows as 'Expiring soon': its own number, else the Settings one."""
    return policy.remind_days or cfg.insurance_alert_days or 60


def _get_visible(user, pk):
    return get_object_or_404(_scoped(user), pk=pk)


def _plus_year(d):
    try:
        return d.replace(year=d.year + 1) - timedelta(days=1)
    except ValueError:  # 29 February
        return d.replace(year=d.year + 1, day=28) - timedelta(days=1)


# --------------------------------------------------------------------------
# Files
# --------------------------------------------------------------------------
def check_files(files):
    """Returns a list of problems with the chosen files (empty when all are fine)."""
    problems = []
    for f in files:
        ext = f.name.lower().rsplit(".", 1)[-1] if "." in f.name else ""
        if ext not in SIGNATURES:
            problems.append(f"{f.name}: only PDF, JPG, PNG or WebP files are allowed.")
            continue
        if f.size > MAX_FILE_MB * 1024 * 1024:
            problems.append(f"{f.name}: larger than {MAX_FILE_MB} MB.")
            continue
        head = f.read(12)
        f.seek(0)
        ok = head.startswith(SIGNATURES[ext]) and (ext != "webp" or head[8:12] == b"WEBP")
        if not ok:
            problems.append(f"{f.name}: this does not look like a real {ext.upper()} file.")
    return problems


def save_files(policy, files, user):
    for f in files:
        InsuranceDocument.objects.create(policy=policy, file=f, name=f.name[:200], size=f.size, uploaded_by=user)


# --------------------------------------------------------------------------
# Form
# --------------------------------------------------------------------------
class PolicyForm(forms.ModelForm):
    class Meta:
        model = InsurancePolicy
        fields = ["centres", "cover_type", "insurer", "policy_number", "contact", "start_date", "expiry_date",
                  "sum_insured", "premium", "responsible", "remind_days", "equipment", "notes", "cancelled"]
        widgets = {
            "start_date": forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"),
            "expiry_date": forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"),
            "notes": forms.Textarea(attrs={"rows": 3}),
            "equipment": forms.SelectMultiple(attrs={"size": 6}),
            "responsible": forms.SelectMultiple(attrs={"size": 5}),
            "centres": forms.SelectMultiple(attrs={"size": 6}),
        }

    def __init__(self, *args, show_money=True, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["centres"].queryset = Centre.objects.filter(active=True)
        self.fields["remind_days"].help_text = (
            "Optional. Leave empty to use the number on the Settings page. Reminders then follow 30 and 7 days "
            "before expiry, on the day and the day after, as far as they come after this number.")
        self.fields["centres"].help_text = ("Hold Ctrl (Command on a Mac) to choose several. Choose none for a policy "
                                            "that covers all centres or the head office.")
        types = InsuranceCoverType.objects.filter(active=True)
        if self.instance.pk:
            types = InsuranceCoverType.objects.filter(Q(active=True) | Q(pk=self.instance.cover_type_id))
        self.fields["cover_type"].queryset = types
        self.fields["responsible"].queryset = User.objects.filter(is_active=True).exclude(role=User.REQUESTER)
        self.fields["responsible"].help_text = ("Optional. Hold Ctrl (Command on a Mac) to choose several. They also "
                                                "receive the expiry reminders if they have Insurance access.")
        self.fields["equipment"].queryset = Equipment.objects.filter(active=True).select_related("centre")
        if not show_money:
            del self.fields["sum_insured"], self.fields["premium"]

    def clean(self):
        data = super().clean()
        start, end = data.get("start_date"), data.get("expiry_date")
        if start and end and end < start:
            self.add_error("expiry_date", "The expiry date cannot be before the start date.")
        return data


# --------------------------------------------------------------------------
# Register
# --------------------------------------------------------------------------
def _filtered(request):
    """Policies the person may see, narrowed by the filters in the address. Returns (rows, filters)."""
    cfg = SiteSettings.load()
    today = timezone.localdate()
    g = request.GET
    f = {"q": g.get("q", "").strip(), "centre": g.get("centre", ""), "cover": g.get("cover", ""),
         "status": g.get("status", "") if g.get("status", "") in dict(STATUS_FILTERS) else ""}
    qs = _scoped(request.user).annotate(doc_count=Count("documents", distinct=True)).order_by("expiry_date", "insurer", "pk")  # a grouped query ignores the model's own ordering
    if f["q"]:
        qs = qs.filter(Q(insurer__icontains=f["q"]) | Q(policy_number__icontains=f["q"]) |
                       Q(contact__icontains=f["q"]) | Q(notes__icontains=f["q"]))
    if f["centre"] == "0":
        qs = qs.filter(centres__isnull=True)
    elif f["centre"].isdigit():
        qs = qs.filter(Q(centres__id=int(f["centre"])) | Q(centres__isnull=True)).distinct()
    if f["cover"].isdigit():
        qs = qs.filter(cover_type_id=int(f["cover"]))
    rows = []
    for p in qs:
        p.status = p.status_on(today, _soon(p, cfg))
        p.days_left = (p.expiry_date - today).days
        if f["status"] == "" and not p.current:
            continue
        if f["status"] not in ("", "all") and p.status != f["status"]:
            continue
        rows.append(p)
    return rows, f


@viewer
def insurance_list(request):
    user = request.user
    cfg = SiteSettings.load()
    tab = request.GET.get("tab") if request.GET.get("tab") in ("gaps", "types") else "policies"
    if tab == "types" and not user.is_admin:
        return redirect("insurance_list")
    money = _show_money(user, cfg)
    centres = Centre.objects.filter(active=True)
    if not user.insurance_sees_all:
        centres = centres.filter(pk__in=user.centres.all())
    today = timezone.localdate()
    live = [p for p in _scoped(user) if p.current]
    status = [p.status_on(today, _soon(p, cfg)) for p in live]
    ctx = {"tab": tab, "money": money, "centres": centres, "status_filters": STATUS_FILTERS,
           "cover_types": InsuranceCoverType.objects.all(), "can_edit": user.can_manage_insurance, "can_admin": user.is_admin,
           "expiring_count": status.count(InsurancePolicy.EXPIRING),
           "expired_count": status.count(InsurancePolicy.EXPIRED), "current_count": len(live)}
    if tab == "policies":
        rows, f = _filtered(request)
        ctx.update(rows=rows, f=f,
                   total_sum=sum((p.sum_insured or 0) for p in rows) if money else None,
                   total_premium=sum((p.premium or 0) for p in rows) if money else None)
    elif tab == "gaps":
        ctx.update(_gaps(user, centres, cfg))
    else:
        ctx["all_types"] = InsuranceCoverType.objects.all()
    ctx.setdefault("gap_total", _gap_count(user, centres, cfg))
    return render(request, "insurance.html", ctx)


def _gaps(user, centres, cfg):
    """Centres as rows, the cover types marked 'expected at every centre' as columns."""
    today = timezone.localdate()
    types = list(InsuranceCoverType.objects.filter(core=True, active=True))
    live = list(InsurancePolicy.objects.filter(cancelled=False, renewed=False)
                .select_related("cover_type").prefetch_related("centres"))
    for p in live:
        p.centre_ids = {c.pk for c in p.centres.all()}
    visible = set(_scoped(user).values_list("pk", flat=True))
    linked = set(InsurancePolicy.equipment.through.objects.filter(
        insurancepolicy_id__in=[p.pk for p in live if p.expiry_date >= today]).values_list("equipment_id", flat=True))
    machines = {}
    for e in Equipment.objects.filter(active=True, centre__in=centres):
        machines.setdefault(e.centre_id, []).append(e.pk)
    rows, gaps = [], 0
    for c in centres:
        cells = []
        for t in types:
            best = None
            for p in live:
                if p.cover_type_id == t.pk and (not p.centre_ids or c.pk in p.centre_ids):
                    if best is None or p.expiry_date > best.expiry_date:
                        best = p
            state = best.status_on(today, _soon(best, cfg)) if best else "none"
            if state in ("none", "expired"):
                gaps += 1
            cells.append({"type": t, "state": state, "policy": best, "everywhere": bool(best and not best.centre_ids),
                          "link": bool(best and best.pk in visible)})
        ids = machines.get(c.pk, [])
        rows.append({"centre": c, "cells": cells, "machines": len(ids),
                     "unlinked": sum(1 for i in ids if i not in linked)})
    return {"gap_types": types, "gap_rows": rows, "gap_total": gaps}


def _gap_count(user, centres, cfg):
    """The number shown on the Coverage grid tab: missing or expired cover across the centres in view."""
    return _gaps(user, centres, cfg)["gap_total"]


# --------------------------------------------------------------------------
# One policy
# --------------------------------------------------------------------------
@viewer
def insurance_detail(request, pk):
    p = _get_visible(request.user, pk)
    cfg = SiteSettings.load()
    today = timezone.localdate()
    p.status = p.status_on(today, _soon(p, cfg))
    p.days_left = (p.expiry_date - today).days
    return render(request, "insurance_detail.html", {
        "p": p, "money": _show_money(request.user, cfg), "can_edit": request.user.can_manage_insurance, "can_admin": request.user.is_admin,
        "docs": p.documents.all(), "machines": p.equipment.select_related("centre"), "soon": _soon(p, cfg),
        "stages": insurance_stages(_soon(p, cfg)),
        "renewed_from": p.renewed_from, "renewals": p.renewals.all(), "max_mb": MAX_FILE_MB})


@manager
def insurance_new(request):
    cfg = SiteSettings.load()
    money = _show_money(request.user, cfg)
    old = None
    renew = request.GET.get("renew") or request.POST.get("renew")
    if renew and renew.isdigit():
        old = get_object_or_404(InsurancePolicy, pk=int(renew))
    initial = {}
    if old:
        start = old.expiry_date + timedelta(days=1)
        initial = {"centres": list(old.centres.all()), "cover_type": old.cover_type, "insurer": old.insurer,
                   "policy_number": old.policy_number, "contact": old.contact, "start_date": start,
                   "expiry_date": _plus_year(start), "sum_insured": old.sum_insured, "premium": old.premium,
                   "responsible": list(old.responsible.all()), "remind_days": old.remind_days,
                   "equipment": list(old.equipment.all())}
    form = PolicyForm(request.POST or None, initial=initial, show_money=money)
    files = request.FILES.getlist("files")
    problems = []
    if request.method == "POST":
        if not files:
            problems.append("Attach the policy document (PDF or photo). It is required.")
        problems += check_files(files)
        if form.is_valid() and not problems:
            p = form.save(commit=False)
            p.created_by = request.user
            if old:
                p.renewed_from = old
            p.save()
            form.save_m2m()
            save_files(p, files, request.user)
            if old:
                old.renewed = True
                old.save(update_fields=["renewed"])
            messages.success(request, "Policy saved." if not old else "Policy renewed. The old policy is marked Renewed.")
            return redirect("insurance_detail", pk=p.pk)
        messages.error(request, "The policy was not saved. Check the marked fields.")
    return render(request, "insurance_form.html", {"form": form, "old": old, "problems": problems, "money": money,
                                                   "max_mb": MAX_FILE_MB, "new": True})


@manager
def insurance_edit(request, pk):
    p = get_object_or_404(InsurancePolicy, pk=pk)
    money = _show_money(request.user)
    form = PolicyForm(request.POST or None, instance=p, show_money=money)
    files = request.FILES.getlist("files")
    problems = check_files(files) if request.method == "POST" else []
    if request.method == "POST":
        if form.is_valid() and not problems:
            form.save()
            save_files(p, files, request.user)
            messages.success(request, "Policy saved.")
            return redirect("insurance_detail", pk=p.pk)
        messages.error(request, "The policy was not saved. Check the marked fields.")
    return render(request, "insurance_form.html", {"form": form, "p": p, "problems": problems, "money": money,
                                                   "max_mb": MAX_FILE_MB, "new": False})


@admin_only
@require_POST
def insurance_delete(request, pk):
    p = get_object_or_404(InsurancePolicy, pk=pk)
    for d in p.documents.all():
        d.file.delete(save=False)
    if p.renewed_from_id and not p.renewals.exists():  # deleting a renewal: the earlier policy is current again
        InsurancePolicy.objects.filter(pk=p.renewed_from_id).update(renewed=False)
    p.delete()
    messages.success(request, "Policy deleted.")
    return redirect("insurance_list")


# --------------------------------------------------------------------------
# Documents
# --------------------------------------------------------------------------
@manager
@require_POST
def insurance_doc_add(request, pk):
    p = get_object_or_404(InsurancePolicy, pk=pk)
    files = request.FILES.getlist("files")
    problems = check_files(files) if files else ["Choose a file first."]
    if problems:
        for x in problems:
            messages.error(request, x)
    else:
        save_files(p, files, request.user)
        messages.success(request, f"{len(files)} file{'s' if len(files) != 1 else ''} added.")
    return redirect("insurance_detail", pk=p.pk)


@manager
@require_POST
def insurance_doc_delete(request, pk):
    d = get_object_or_404(InsuranceDocument, pk=pk)
    p = d.policy
    if p.documents.count() <= 1:
        messages.error(request, "A policy must keep at least one document. Add the new one first, then remove this one.")
    else:
        d.file.delete(save=False)
        d.delete()
        messages.success(request, "Document removed.")
    return redirect("insurance_detail", pk=p.pk)


@viewer
def insurance_doc(request, pk):
    d = get_object_or_404(InsuranceDocument.objects.select_related("policy"), pk=pk)
    if not _scoped(request.user).filter(pk=d.policy_id).exists():
        raise Http404
    try:
        return _safe(FileResponse(d.file.open("rb"), filename=d.name, as_attachment=not (d.is_image or d.name.lower().endswith(".pdf"))))
    except FileNotFoundError:
        raise Http404


def machine_policies(user, equipment):
    """The current policies that list this machine, for its page. None when the person may not see insurance."""
    if not user.can_view_insurance:
        return None
    cfg = SiteSettings.load()
    today = timezone.localdate()
    rows = list(_scoped(user).filter(equipment=equipment, cancelled=False, renewed=False))
    for p in rows:
        p.status = p.status_on(today, _soon(p, cfg))
        p.days_left = (p.expiry_date - today).days
    return rows


# --------------------------------------------------------------------------
# Cover types (administrators)
# --------------------------------------------------------------------------
@admin_only
@require_POST
def insurance_types_action(request):
    p = request.POST
    name = p.get("name", "").strip()
    if p.get("action") == "add":
        if not name:
            messages.error(request, "Enter a name for the cover type.")
        elif InsuranceCoverType.objects.filter(name__iexact=name).exists():
            messages.error(request, f"'{name}' already exists.")
        else:
            InsuranceCoverType.objects.create(name=name[:80], core=bool(p.get("core")))
            messages.success(request, f"'{name}' added.")
    elif p.get("action") == "save":
        t = get_object_or_404(InsuranceCoverType, pk=p.get("pk"))
        if not name:
            messages.error(request, "The name cannot be empty.")
        elif InsuranceCoverType.objects.filter(name__iexact=name).exclude(pk=t.pk).exists():
            messages.error(request, f"'{name}' already exists.")
        else:
            t.name, t.core, t.active = name[:80], bool(p.get("core")), bool(p.get("active"))
            t.save()
            messages.success(request, "Saved.")
    return redirect("/insurance/?tab=types")


# --------------------------------------------------------------------------
# Excel
# --------------------------------------------------------------------------
@viewer
def insurance_export(request):
    from openpyxl import Workbook
    from openpyxl.styles import Font
    money = _show_money(request.user)
    rows, _ = _filtered(request)
    wb = Workbook()
    ws = wb.active
    ws.title = "Insurance"
    head = ["Centres", "Cover type", "Insurer", "Policy number", "Broker or agent", "Start date", "Expiry date",
            "Days left", "Status", "Responsible", "Machines covered"]
    if money:
        head += ["Sum insured", "Premium"]
    ws.append(head)
    for c in ws[1]:
        c.font = Font(bold=True)
    for p in rows:
        line = [p.location, p.cover_type.name, p.insurer, p.policy_number,
                p.contact, p.start_date, p.expiry_date, p.days_left, InsurancePolicy.STATUS_LABELS[p.status],
                ", ".join(u.display_name for u in p.responsible.all()),
                ", ".join(e.name for e in p.equipment.all())]
        if money:
            line += [float(p.sum_insured) if p.sum_insured is not None else None,
                     float(p.premium) if p.premium is not None else None]
        ws.append(line)
    for col in ("F", "G"):
        for cell in ws[col][1:]:
            cell.number_format = "dd mmm yyyy"
    for i, w in enumerate([22, 24, 24, 20, 24, 14, 14, 10, 14, 20, 40, 16, 14][:len(head)], start=1):
        ws.column_dimensions[chr(64 + i)].width = w
    buf = io.BytesIO()
    wb.save(buf)
    resp = HttpResponse(buf.getvalue(), content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    resp["Content-Disposition"] = f'attachment; filename="insurance-{date.today():%Y-%m-%d}.xlsx"'
    return resp
