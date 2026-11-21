"""Licences and certificates (Oversee > Insurance > Licences): AERB, biomedical waste, fire NOC and the like.

Same people and the same rules as insurance: administrators and anyone given Insurance access. View own centres also sees
licences that list no centres; Manage may add, edit and renew; deleting is for administrators."""
import io
from datetime import date, timedelta

from django import forms
from django.contrib import messages
from django.db.models import Count, Q
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from .models import Centre, Equipment, Licence, LicenceDocument, User
from .views import admin_only
from .views_insurance import MAX_FILE_MB, PolicyForm, check_files, manager, serve_document, viewer, _plus_year

STATUS_FILTERS = [("", "Current (not renewed or closed)"), ("active", "Valid"), ("expiring", "Expiring soon"),
                  ("expired", "Expired"), ("renewed", "Renewed"), ("closed", "Closed"), ("all", "Everything")]


def _scoped(user):
    qs = Licence.objects.all()
    if user.insurance_sees_all:
        return qs
    return qs.filter(Q(centres__in=user.centres.all()) | Q(centres__isnull=True)).distinct()


def save_files(licence, files, user):
    for f in files:
        LicenceDocument.objects.create(licence=licence, file=f, name=f.name[:200], size=f.size, uploaded_by=user)


class LicenceForm(forms.ModelForm):
    class Meta:
        model = Licence
        fields = ["name", "issuer", "reference_number", "centres", "equipment", "issued_on", "valid_until", "responsible",
                  "remind_days", "notes", "closed"]
        widgets = {"issued_on": forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"),
                   "valid_until": forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"),
                   "notes": forms.Textarea(attrs={"rows": 3}),
                   "centres": forms.CheckboxSelectMultiple, "responsible": forms.CheckboxSelectMultiple}

    checklist = PolicyForm.checklist

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.fields["name"].widget.attrs["list"] = "licence-names"
        self.fields["responsible"].queryset = User.objects.filter(is_active=True).exclude(role=User.REQUESTER)
        self.fields["equipment"].queryset = Equipment.objects.filter(active=True).select_related("centre").order_by("centre__name", "name")
        self.fields["equipment"].label_from_instance = lambda e: f"{e.name} · {e.centre.code}"
        self.fields["centres"].help_text = ("Tick the centres it covers, or Select all. Tick none for one that covers every "
                                            "centre or the head office.")
        if not self.instance.pk:
            del self.fields["closed"]

    centres_items = property(lambda self: self.checklist("centres"))
    responsible_items = property(lambda self: self.checklist("responsible"))

    def clean(self):
        data = super().clean()
        start, end = data.get("issued_on"), data.get("valid_until")
        if start and end and end < start:
            self.add_error("valid_until", "It cannot end before it was issued.")
        name, ref = (data.get("name") or "").strip(), (data.get("reference_number") or "").strip()
        if name and start:
            same = Licence.objects.filter(name__iexact=name, reference_number__iexact=ref, issued_on=start)
            if self.instance.pk:
                same = same.exclude(pk=self.instance.pk)
            if same.exists():
                self.add_error("name", "This licence is already in the register (same name, number and issue date). "
                                       "Open it instead of adding it twice.")
        return data


# --------------------------------------------------------------------------
# List
# --------------------------------------------------------------------------
def _filtered(request):
    user, today = request.user, timezone.localdate()
    g = request.GET
    f = {"q": g.get("q", "").strip(), "centre": g.get("centre", ""),
         "status": g.get("status", "") if g.get("status", "") in dict(STATUS_FILTERS) else ""}
    qs = _scoped(user).annotate(doc_count=Count("documents", distinct=True)).prefetch_related("centres", "responsible")\
        .select_related("equipment").order_by("valid_until", "name", "pk")
    if f["q"]:
        qs = qs.filter(Q(name__icontains=f["q"]) | Q(issuer__icontains=f["q"]) | Q(reference_number__icontains=f["q"]))
    if f["centre"] == "0":
        qs = qs.filter(centres__isnull=True)
    elif f["centre"].isdigit():
        qs = qs.filter(Q(centres__pk=int(f["centre"])) | Q(centres__isnull=True))
    rows = []
    for x in qs.distinct():
        x.days_left = (x.valid_until - today).days
        x.status = x.status_on(today)
        if f["status"] == "" and not x.current:
            continue
        if f["status"] not in ("", "all") and x.status != f["status"]:
            continue
        rows.append(x)
    return rows, f


@viewer
def licence_list(request):
    user = request.user
    rows, f = _filtered(request)
    today = timezone.localdate()
    live = [x for x in _scoped(user) if x.current]
    st = [x.status_on(today) for x in live]
    centres = Centre.objects.filter(active=True)
    if not user.insurance_sees_all:
        centres = centres.filter(pk__in=user.centres.all())
    return render(request, "licences.html", {
        "rows": rows, "f": f, "centres": centres, "status_filters": STATUS_FILTERS, "can_edit": user.can_manage_insurance,
        "current_count": len(live), "expiring_count": st.count(Licence.EXPIRING), "expired_count": st.count(Licence.EXPIRED)})


@viewer
def licence_export(request):
    from openpyxl import Workbook
    from openpyxl.styles import Font
    rows, _ = _filtered(request)
    wb = Workbook()
    ws = wb.active
    ws.title = "Licences"
    head = ["Licence or certificate", "Issued by", "Number", "Centres", "Machine", "Issued on", "Valid until", "Days left",
            "Status", "Responsible"]
    ws.append(head)
    for c in ws[1]:
        c.font = Font(bold=True)
    for x in rows:
        ws.append([x.name, x.issuer, x.reference_number, x.location, x.equipment.name if x.equipment else "", x.issued_on,
                   x.valid_until, x.days_left, Licence.STATUS_LABELS[x.status],
                   ", ".join(u.display_name for u in x.responsible.all())])
    for col in ("F", "G"):
        for cell in ws[col][1:]:
            cell.number_format = "dd mmm yyyy"
    for i, w in enumerate([34, 26, 20, 26, 24, 14, 14, 10, 14, 24], start=1):
        ws.column_dimensions[chr(64 + i)].width = w
    buf = io.BytesIO()
    wb.save(buf)
    resp = HttpResponse(buf.getvalue(), content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    resp["Content-Disposition"] = f'attachment; filename="licences-{date.today():%Y-%m-%d}.xlsx"'
    return resp


# --------------------------------------------------------------------------
# One licence
# --------------------------------------------------------------------------
@viewer
def licence_detail(request, pk):
    x = get_object_or_404(_scoped(request.user), pk=pk)
    today = timezone.localdate()
    x.days_left = (x.valid_until - today).days
    x.status = x.status_on(today)
    return render(request, "licence_detail.html", {
        "x": x, "docs": x.documents.all(), "can_edit": request.user.can_manage_insurance, "can_admin": request.user.is_admin,
        "renewed_from": x.renewed_from, "renewals": x.renewals.all(), "max_mb": MAX_FILE_MB,
        "reminders": sorted({d for d in (x.first_reminder, 30, 7, 0) if d <= x.first_reminder}, reverse=True)})


@manager
def licence_new(request):
    old = None
    renew = request.GET.get("renew") or request.POST.get("renew")
    if renew and renew.isdigit():
        old = get_object_or_404(Licence, pk=int(renew))
    initial = {}
    if old:
        start = old.valid_until + timedelta(days=1)
        term = max((old.valid_until - old.issued_on).days, 1)
        initial = {"name": old.name, "issuer": old.issuer, "reference_number": old.reference_number,
                   "centres": list(old.centres.all()), "equipment": old.equipment, "issued_on": start,
                   "valid_until": start + timedelta(days=term), "responsible": list(old.responsible.all()),
                   "remind_days": old.remind_days}
    form = LicenceForm(request.POST or None, initial=initial)
    files = request.FILES.getlist("files")
    problems = []
    if request.method == "POST":
        if not files:
            problems.append("Attach the licence or certificate (PDF or photo). It is required.")
        problems += check_files(files)
        if form.is_valid() and not problems:
            x = form.save(commit=False)
            x.created_by = request.user
            if old:
                x.renewed_from = old
            x.save()
            form.save_m2m()
            save_files(x, files, request.user)
            if old:
                old.renewed = True
                old.save(update_fields=["renewed"])
            messages.success(request, "Saved." if not old else "Renewed. The old one is marked Renewed.")
            return redirect("licence_detail", pk=x.pk)
        messages.error(request, "It was not saved. Check the marked fields.")
    return render(request, "licence_form.html", {"form": form, "old": old, "problems": problems, "max_mb": MAX_FILE_MB,
                                                 "new": True, "suggestions": Licence.SUGGESTIONS})


@manager
def licence_edit(request, pk):
    x = get_object_or_404(_scoped(request.user), pk=pk)
    form = LicenceForm(request.POST or None, instance=x)
    files = request.FILES.getlist("files")
    problems = check_files(files) if request.method == "POST" and files else []
    if request.method == "POST":
        if form.is_valid() and not problems:
            form.save()
            save_files(x, files, request.user)
            messages.success(request, "Saved.")
            return redirect("licence_detail", pk=x.pk)
        messages.error(request, "It was not saved. Check the marked fields.")
    return render(request, "licence_form.html", {"form": form, "x": x, "problems": problems, "max_mb": MAX_FILE_MB,
                                                 "suggestions": Licence.SUGGESTIONS})


@admin_only
@require_POST
def licence_delete(request, pk):
    x = get_object_or_404(Licence, pk=pk)
    for d in x.documents.all():
        d.file.delete(save=False)
    x.delete()
    messages.success(request, "Deleted.")
    return redirect("licence_list")


@manager
@require_POST
def licence_doc_add(request, pk):
    x = get_object_or_404(_scoped(request.user), pk=pk)
    files = request.FILES.getlist("files")
    problems = check_files(files) if files else ["Choose a file first."]
    if problems:
        for p in problems:
            messages.error(request, p)
    else:
        save_files(x, files, request.user)
        messages.success(request, f"{len(files)} file{'s' if len(files) != 1 else ''} added.")
    return redirect("licence_detail", pk=x.pk)


@manager
@require_POST
def licence_doc_delete(request, pk):
    d = get_object_or_404(LicenceDocument, pk=pk)
    x = d.licence
    if x.documents.count() <= 1:
        messages.error(request, "It must keep at least one document. Add the new one first, then remove this one.")
    else:
        d.file.delete(save=False)
        d.delete()
        messages.success(request, "Document removed.")
    return redirect("licence_detail", pk=x.pk)


@viewer
def licence_doc(request, pk):
    d = get_object_or_404(LicenceDocument.objects.select_related("licence"), pk=pk)
    if not _scoped(request.user).filter(pk=d.licence_id).exists():
        raise Http404
    return serve_document(request, d)
