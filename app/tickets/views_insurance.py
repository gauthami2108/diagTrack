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
from decimal import Decimal, InvalidOperation

from django import forms
from django.contrib import messages
from django.core.paginator import Paginator
from django.db.models import Count, Q
from django.http import FileResponse, Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from .models import (Centre, ClaimDocument, PremiumInstalment, RenewalQuote, Equipment, InsuranceClaim, InsuranceCoverType, InsuranceDocument, InsurancePolicy, Licence,
                     SiteSettings, User)
from . import history
from . import insurance_log as ilog
from .services import insurance_stages
from .views import _safe, admin_only, role_required

MAX_FILE_MB = 20
# Allowed uploads: extension -> what the first bytes of a real file of that kind look like.
SIGNATURES = {"pdf": b"%PDF", "jpg": b"\xff\xd8\xff", "jpeg": b"\xff\xd8\xff", "png": b"\x89PNG", "webp": b"RIFF"}
STATUS_FILTERS = [("", "Current (not renewed or cancelled)"), ("active", "Active"), ("expiring", "Expiring soon"),
                  ("expired", "Expired"), ("renewed", "Renewed"), ("cancelled", "Cancelled"), ("all", "Everything"),
                  ("archived", "Archived")]


def status_filters_for(user):
    """Archived records are listed only for people who can restore them."""
    return [(v, l) for v, l in STATUS_FILTERS if v != "archived" or user.can_manage_insurance]

viewer = role_required(lambda u: u.can_view_insurance)
manager = role_required(lambda u: u.can_manage_insurance)


def _show_money(user, cfg=None):
    cfg = cfg or SiteSettings.load()
    return bool(user.insurance_money and cfg.insurance_show_money)


def _scoped(user, archived=False):
    """Policies this person may see. Archived ones are kept out unless asked for: archived=True gives only those,
    archived=None gives both (used for opening an archived policy's documents)."""
    qs = InsurancePolicy.objects.select_related("cover_type").prefetch_related("centres", "responsible")
    if archived is not None:
        qs = qs.filter(archived_at__isnull=not archived)
    if user.insurance_sees_all:
        return qs
    # Own centres, plus policies that cover every centre or the head office (they have no centres listed)
    return qs.filter(Q(centres__in=user.centres.all()) | Q(centres__isnull=True)).distinct()


def _soon(policy, cfg):
    """Days before expiry at which a policy shows as 'Expiring soon': its own number, else the Settings one."""
    return policy.remind_days or cfg.insurance_alert_days or 60


def _get_visible(user, pk):
    """A policy this person may open. An archived one opens only for people who may restore it."""
    qs = _scoped(user, archived=None)
    p = get_object_or_404(qs, pk=pk)
    if p.archived_at and not user.can_manage_insurance:
        raise Http404
    return p


def _can_open(user, policy_id):
    return _get_visible_or_none(user, policy_id) is not None


def _get_visible_or_none(user, pk):
    try:
        return _get_visible(user, pk)
    except Http404:
        return None


def _same_day_next_year(d):
    try:
        return d.replace(year=d.year + 1)
    except ValueError:  # 29 February
        return date(d.year + 1, 2, 28)


def _plus_year(d):
    try:
        return d.replace(year=d.year + 1) - timedelta(days=1)
    except ValueError:  # 29 February: the next year has no such day, so the policy ends on 28 February
        return date(d.year + 1, 3, 1) - timedelta(days=1)


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
                  "sum_insured", "premium", "premium_due", "responsible", "remind_days", "equipment", "covers_all_machines",
                  "covers", "notes", "cancelled"]
        widgets = {
            "start_date": forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"),
            "premium_due": forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"),
            "expiry_date": forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"),
            "notes": forms.Textarea(attrs={"rows": 3}),
            "equipment": forms.CheckboxSelectMultiple,
            "responsible": forms.CheckboxSelectMultiple,
            "centres": forms.CheckboxSelectMultiple,
        }

    def __init__(self, *args, show_money=True, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["centres"].queryset = Centre.objects.filter(active=True)
        self.fields["remind_days"].help_text = (
            "Optional. Leave empty to use the number on the Settings page. Reminders then follow 30 and 7 days "
            "before expiry, on the day and the day after, as far as they come after this number.")
        self.warnings = []
        self.fields["centres"].help_text = ("Tick the centres this policy covers, or Select all. Tick none for a policy "
                                            "that covers all centres or the head office.")
        types = InsuranceCoverType.objects.filter(active=True)
        if self.instance.pk:
            types = InsuranceCoverType.objects.filter(Q(active=True) | Q(pk=self.instance.cover_type_id))
        self.fields["cover_type"].queryset = types
        self.fields["responsible"].queryset = User.objects.filter(is_active=True).exclude(role=User.REQUESTER)
        self.fields["responsible"].help_text = ("Optional. Tick one or more people. They also receive the expiry and "
                                                "premium reminders if they have Insurance access.")
        self.fields["equipment"].queryset = Equipment.objects.filter(active=True).select_related("centre")
        if self.instance.pk and self.instance.instalments.exists():
            self.fields["premium_due"].disabled = True
            self.fields["premium_due"].help_text = "Set from the instalment schedule on the policy page: the earliest instalment not yet paid."
        if not show_money:
            del self.fields["sum_insured"], self.fields["premium"]
        if self.instance.pk:
            from . import history
            others = InsurancePolicy.objects.filter(cover_type_id=self.instance.cover_type_id, archived_at__isnull=True)\
                .exclude(pk__in=history.descendants(self.instance) | {self.instance.pk}).order_by("-start_date")
            self.fields["earlier"] = forms.ModelChoiceField(
                others, required=False, initial=self.instance.renewed_from_id, label="Earlier policy this one continues",
                help_text="Only for a policy that was not renewed through DiagTrack: pick the policy it replaced so the "
                          "History tab shows them together. The earlier one is then marked Renewed.")
            self.fields["earlier"].label_from_instance = lambda o: f"{o.insurer} {o.policy_number}, {o.start_date:%d %b %Y} to {o.expiry_date:%d %b %Y}, {o.location}"

    def checklist(self, name):
        """The choices of a tick-list field as plain dicts, so the page can draw them as tick boxes."""
        selected = {str(getattr(v, "pk", v)) for v in (self[name].value() or [])}
        items = []
        for o in self.fields[name].queryset:
            if name == "centres":
                label = f"{o.name} ({o.code})"
            elif name == "equipment":
                label = f"{o.name} · {o.centre.code}"
            else:
                label = o.display_name
            items.append({"value": o.pk, "label": label, "checked": str(o.pk) in selected,
                          "centre": o.centre_id if name == "equipment" else ""})
        return items

    centres_items = property(lambda self: self.checklist("centres"))
    responsible_items = property(lambda self: self.checklist("responsible"))
    equipment_items = property(lambda self: self.checklist("equipment"))

    def clean(self):
        data = super().clean()
        start, end = data.get("start_date"), data.get("expiry_date")
        if start and end and end < start:
            self.add_error("expiry_date", "The expiry date cannot be before the start date.")
        insurer, number = (data.get("insurer") or "").strip(), (data.get("policy_number") or "").strip()
        if insurer and number and start:
            same = InsurancePolicy.objects.filter(insurer__iexact=insurer, policy_number__iexact=number, start_date=start)
            if self.instance.pk:
                same = same.exclude(pk=self.instance.pk)
            if same.exists():
                self.add_error("policy_number", "This policy is already in the register (same insurer, policy number and "
                                                "start date). Open it instead of adding it twice.")
        self.warnings = self._date_warnings(data, start, end)
        return data

    def _date_warnings(self, data, start, end):
        """Dates that are possible but unusual. They never block saving: the page shows them live and again after saving."""
        out = []
        today = timezone.localdate()
        if start and end and end >= start:
            days = (end - start).days + 1
            if days > 1096:
                out.append(f"The policy runs for about {days / 365:.1f} years. Most policies run for one year.")
            elif days < 30:
                out.append(f"The policy runs for only {days} day{'s' if days != 1 else ''}.")
            if end < today and (not self.instance.pk or "expiry_date" in self.changed_data):
                out.append("The expiry date has already passed, so this policy will show as Expired.")
        due = data.get("premium_due")
        if due and start and end and not (start <= due <= end):
            out.append("The next premium due date is outside the policy dates.")
        return out


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
    if f["status"] == "archived" and not request.user.can_manage_insurance:
        f["status"] = ""
    qs = _scoped(request.user, archived=f["status"] == "archived").annotate(
        doc_count=Count("documents", distinct=True)).order_by("expiry_date", "insurer", "pk")  # a grouped query ignores the model's own ordering
    if f["q"]:
        qs = qs.filter(Q(insurer__icontains=f["q"]) | Q(policy_number__icontains=f["q"]) |
                       Q(contact__icontains=f["q"]) | Q(notes__icontains=f["q"]) | Q(covers__icontains=f["q"]))
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
        if f["status"] not in ("", "all", "archived") and p.status != f["status"]:
            continue
        rows.append(p)
    return rows, f


@viewer
def insurance_list(request):
    user = request.user
    cfg = SiteSettings.load()
    tab = request.GET.get("tab") if request.GET.get("tab") in ("gaps", "types", "claims") else "policies"
    if tab == "types" and not user.is_admin:
        return redirect("insurance_list")
    money = _show_money(user, cfg)
    centres = Centre.objects.filter(active=True)
    if not user.insurance_sees_all:
        centres = centres.filter(pk__in=user.centres.all())
    today = timezone.localdate()
    live = [p for p in _scoped(user) if p.current]
    status = [p.status_on(today, _soon(p, cfg)) for p in live]
    ctx = {"tab": tab, "money": money, "centres": centres, "status_filters": status_filters_for(user),
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
        ctx.update(uninsured=uninsured(user, centres, cfg), min_value=cfg.insurance_min_value)
    elif tab == "claims":
        claims, cf = _claims(request)
        ctx.update(claims=claims, cf=cf, claim_statuses=InsuranceClaim.STATUSES)
    else:
        ctx["all_types"] = InsuranceCoverType.objects.annotate(n_policies=Count("policies", filter=Q(policies__archived_at__isnull=True)))
    ctx.setdefault("gap_total", _gap_count(user, centres, cfg))
    ctx["open_claims"] = InsuranceClaim.objects.filter(policy__in=_scoped(user), status__in=InsuranceClaim.OPEN).count()
    return render(request, "insurance.html", ctx)


# --------------------------------------------------------------------------
# Claims
# --------------------------------------------------------------------------
def _claims(request):
    """Claims on the policies this person may open. By default the open ones; ?cstatus=all or a single status."""
    g = request.GET
    cstatus = g.get("cstatus", "open")
    if cstatus not in ("open", "all", *dict(InsuranceClaim.STATUSES)):
        cstatus = "open"
    qs = (InsuranceClaim.objects.filter(policy__in=_scoped(request.user))
          .select_related("policy", "policy__cover_type", "equipment").prefetch_related("policy__centres", "documents"))
    if cstatus == "open":
        qs = qs.filter(status__in=InsuranceClaim.OPEN)
    elif cstatus != "all":
        qs = qs.filter(status=cstatus)
    q = g.get("q", "").strip()
    if q:
        qs = qs.filter(Q(description__icontains=q) | Q(claim_number__icontains=q) | Q(policy__insurer__icontains=q)
                       | Q(policy__policy_number__icontains=q) | Q(equipment__name__icontains=q))
    return list(qs), {"cstatus": cstatus, "q": q}


class ClaimForm(forms.ModelForm):
    class Meta:
        model = InsuranceClaim
        fields = ["incident_date", "description", "equipment", "claim_number", "claimed_on", "status", "amount_claimed",
                  "amount_settled", "notes"]
        widgets = {"incident_date": forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"),
                   "claimed_on": forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"),
                   "notes": forms.Textarea(attrs={"rows": 3})}

    def __init__(self, *a, policy, show_money, **kw):
        super().__init__(*a, **kw)
        self.policy = policy
        machines = policy.equipment.all()
        if not machines:
            machines = Equipment.objects.filter(active=True)
            if policy.centres.exists():
                machines = machines.filter(centre__in=policy.centres.all())
        self.fields["equipment"].queryset = machines.select_related("centre").order_by("centre__name", "name")
        self.fields["equipment"].label_from_instance = lambda e: f"{e.name} · {e.centre.code}"
        self.fields["equipment"].empty_label = "Not about one machine"
        if not show_money:
            del self.fields["amount_claimed"], self.fields["amount_settled"]

    def clean(self):
        data = super().clean()
        inc, filed = data.get("incident_date"), data.get("claimed_on")
        if inc and inc > timezone.localdate():
            self.add_error("incident_date", "The incident cannot be in the future.")
        if inc and filed and filed < inc:
            self.add_error("claimed_on", "The claim cannot be filed before the incident.")
        return data

    def warning(self):
        inc = self.cleaned_data.get("incident_date")
        if inc and not (self.policy.start_date <= inc <= self.policy.expiry_date):
            return ("The incident date is outside this policy's dates "
                    f"({self.policy.start_date:%d %b %Y} to {self.policy.expiry_date:%d %b %Y}). Check that it is on the right policy.")


def _claim_line(c):
    return f"{c.description} (incident {c.incident_date:%d %b %Y})"


@manager
def claim_new(request, pk):
    p = get_object_or_404(_scoped(request.user), pk=pk)
    return _claim_form(request, p, None)


@manager
def claim_edit(request, pk):
    c = get_object_or_404(InsuranceClaim.objects.select_related("policy"), pk=pk)
    p = get_object_or_404(_scoped(request.user), pk=c.policy_id)
    return _claim_form(request, p, c)


def _claim_form(request, p, claim):
    money = _show_money(request.user)
    before = None
    if claim:
        before = (claim.status, claim.claim_number, claim.incident_date, claim.claimed_on, claim.equipment_id,
                  claim.description, claim.amount_claimed, claim.amount_settled)
    form = ClaimForm(request.POST or None, instance=claim, policy=p, show_money=money)
    files = request.FILES.getlist("files")
    problems = check_files(files) if request.method == "POST" and files else []
    if request.method == "POST" and form.is_valid() and not problems:
        c = form.save(commit=False)
        c.policy = p
        if not claim:
            c.created_by = request.user
        c.save()
        for f in files:
            ClaimDocument.objects.create(claim=c, file=f, name=f.name[:200], size=f.size, uploaded_by=request.user)
        if files:
            ilog.record(p, request.user, f"Added {len(files)} document{'s' if len(files) != 1 else ''} to claim "
                                         f"{_claim_line(c)}: " + ", ".join(f.name for f in files)[:200])
        if not claim:
            ilog.record(p, request.user, f"Claim added: {_claim_line(c)}")
        else:
            bits = []
            if before[0] != c.status:
                bits.append(f"status {dict(InsuranceClaim.STATUSES)[before[0]]} → {c.get_status_display()}")
            if before[1] != c.claim_number:
                bits.append("claim number changed")
            if before[2:6] != (c.incident_date, c.claimed_on, c.equipment_id, c.description):
                bits.append("details changed")
            if before[6:] != (c.amount_claimed, c.amount_settled):
                bits.append("amounts changed")
            if bits:
                ilog.record(p, request.user, f"Claim updated ({_claim_line(c)}): " + ", ".join(bits))
        messages.success(request, "Claim saved.")
        w = form.warning()
        if w:
            messages.warning(request, w)
        return redirect("insurance_detail", pk=p.pk)
    if request.method == "POST":
        messages.error(request, "The claim was not saved. Check the marked fields.")
    return render(request, "insurance_claim_form.html", {"form": form, "p": p, "claim": claim, "money": money,
                                                         "problems": problems, "max_mb": MAX_FILE_MB,
                                                         "docs": claim.documents.all() if claim else []})


@admin_only
@require_POST
def claim_delete(request, pk):
    c = get_object_or_404(InsuranceClaim.objects.select_related("policy"), pk=pk, policy__archived_at__isnull=True)
    p = c.policy
    ilog.record(p, request.user, f"Claim removed: {_claim_line(c)}")
    for d in c.documents.all():
        d.file.delete(save=False)
    c.delete()
    messages.success(request, "Claim removed.")
    return redirect("insurance_detail", pk=p.pk)


@viewer
def claims_export(request):
    from openpyxl import Workbook
    from openpyxl.styles import Font
    money = _show_money(request.user)
    claims, _ = _claims(request)
    wb = Workbook()
    ws = wb.active
    ws.title = "Claims"
    head = ["Policy", "Centres", "Insurer", "Policy number", "Date of incident", "Machine", "What happened", "Claim number",
            "Date filed", "Status"]
    if money:
        head += ["Amount claimed", "Amount settled"]
    ws.append(head)
    for c in ws[1]:
        c.font = Font(bold=True)
    for c in claims:
        line = [c.policy.cover_type.name, c.policy.location, c.policy.insurer, c.policy.policy_number, c.incident_date,
                c.equipment.name if c.equipment else "", c.description, c.claim_number, c.claimed_on, c.get_status_display()]
        if money:
            line += [float(c.amount_claimed) if c.amount_claimed is not None else None,
                     float(c.amount_settled) if c.amount_settled is not None else None]
        ws.append(line)
    for col in ("E", "I"):
        for cell in ws[col][1:]:
            cell.number_format = "dd mmm yyyy"
    for i, w in enumerate([22, 24, 24, 20, 14, 26, 50, 18, 14, 22, 16, 16][:len(head)], start=1):
        ws.column_dimensions[chr(64 + i)].width = w
    buf = io.BytesIO()
    wb.save(buf)
    resp = HttpResponse(buf.getvalue(), content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    resp["Content-Disposition"] = f'attachment; filename="insurance-claims-{date.today():%Y-%m-%d}.xlsx"'
    return resp


def _gaps(user, centres, cfg):
    """Centres as rows, the cover types marked 'expected at every centre' as columns."""
    today = timezone.localdate()
    types = list(InsuranceCoverType.objects.filter(core=True, active=True))
    live = list(InsurancePolicy.objects.filter(cancelled=False, renewed=False, archived_at__isnull=True)
                .select_related("cover_type").prefetch_related("centres"))
    for p in live:
        p.centre_ids = {c.pk for c in p.centres.all()}
    visible = set(_scoped(user).values_list("pk", flat=True))
    linked = covered_machine_ids(today)
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


def covered_machine_ids(today):
    """Machines on a current policy: listed on it, or at a centre of a policy that covers every machine."""
    pols = InsurancePolicy.objects.filter(cancelled=False, renewed=False, archived_at__isnull=True, expiry_date__gte=today)
    ids = set(InsurancePolicy.equipment.through.objects.filter(insurancepolicy__in=pols).values_list("equipment_id", flat=True))
    for p in pols.filter(covers_all_machines=True).prefetch_related("centres"):
        qs = Equipment.objects.filter(active=True)
        centre_ids = [c.pk for c in p.centres.all()]
        if centre_ids:
            qs = qs.filter(centre__in=centre_ids)
        ids |= set(qs.values_list("pk", flat=True))
    return ids


def uninsured(user, centres, cfg):
    """Active machines worth at least the Settings amount that no current policy lists. A blanket policy that lists no
    machines does not count as cover here, which is why the person decides. Empty when the check is switched off."""
    floor = cfg.insurance_min_value
    if not floor:
        return []
    today = timezone.localdate()
    linked = covered_machine_ids(today)
    return [e for e in Equipment.objects.filter(active=True, centre__in=centres, value__gte=floor)
            .select_related("centre").order_by("-value", "name") if e.pk not in linked]


def _gap_count(user, centres, cfg):
    """The number shown on the Coverage grid tab: missing or expired cover across the centres in view."""
    return _gaps(user, centres, cfg)["gap_total"]


# --------------------------------------------------------------------------
# One policy
# --------------------------------------------------------------------------
INSTALMENT_TERMS = [(1, "Monthly"), (3, "Every 3 months"), (6, "Every 6 months"), (12, "Yearly")]


def _add_months(d, n):
    import calendar
    m = d.month - 1 + n
    y, m = d.year + m // 12, m % 12 + 1
    return date(y, m, min(d.day, calendar.monthrange(y, m)[1]))


def _instalment_rows(p, today):
    rows = list(p.instalments.all())
    for i in rows:
        i.state = "paid" if i.paid_on else "overdue" if i.due_date < today else "due"
        i.days = (i.due_date - today).days
    return rows


def _own_policy(request, pk):
    return get_object_or_404(_scoped(request.user), pk=pk)


def _parse_date(v):
    try:
        return date.fromisoformat((v or "").strip())
    except ValueError:
        return None


def _amount(request, name):
    """A rupee amount from the form, only for people who see money; None when empty or not valid."""
    if not _show_money(request.user):
        return None
    try:
        v = Decimal((request.POST.get(name) or "").replace(",", "").strip())
        return v if 0 <= v < Decimal(10) ** 10 else None
    except InvalidOperation:
        return None


@manager
@require_POST
def instalment_add(request, pk):
    p = _own_policy(request, pk)
    due = _parse_date(request.POST.get("due_date"))
    if not due:
        messages.error(request, "Choose the date the instalment is due.")
    else:
        PremiumInstalment.objects.create(policy=p, due_date=due, amount=_amount(request, "amount"))
        p.sync_premium_due()
        ilog.record(p, request.user, f"Instalment due {due:%d %b %Y} added")
        messages.success(request, "Instalment added.")
    return redirect("insurance_detail", pk=p.pk)


@manager
@require_POST
def instalment_generate(request, pk):
    p = _own_policy(request, pk)
    first = _parse_date(request.POST.get("first_due"))
    try:
        count, step = int(request.POST.get("count", "")), int(request.POST.get("step", ""))
    except ValueError:
        count = step = 0
    if not first or not (2 <= count <= 24) or step not in dict(INSTALMENT_TERMS):
        messages.error(request, "Give the first due date, how often, and a number of instalments from 2 to 24.")
        return redirect("insurance_detail", pk=p.pk)
    if p.instalments.exists():
        messages.error(request, "This policy already has a schedule. Remove its instalments first, or add one by one.")
        return redirect("insurance_detail", pk=p.pk)
    each = _amount(request, "amount")
    for n in range(count):
        PremiumInstalment.objects.create(policy=p, due_date=_add_months(first, n * step), amount=each)
    p.sync_premium_due()
    ilog.record(p, request.user, f"Instalment schedule added: {count} from {first:%d %b %Y}")
    messages.success(request, f"{count} instalments added.")
    return redirect("insurance_detail", pk=p.pk)


@manager
@require_POST
def instalment_paid(request, pk):
    i = get_object_or_404(PremiumInstalment.objects.select_related("policy"), pk=pk)
    p = _own_policy(request, i.policy_id)
    i.paid_on = None if i.paid_on else timezone.localdate()
    i.save(update_fields=["paid_on"])
    p.sync_premium_due()
    ilog.record(p, request.user, f"Instalment due {i.due_date:%d %b %Y} " + ("marked paid" if i.paid_on else "marked not paid"))
    return redirect("insurance_detail", pk=p.pk)


@manager
@require_POST
def instalment_delete(request, pk):
    i = get_object_or_404(PremiumInstalment.objects.select_related("policy"), pk=pk)
    p = _own_policy(request, i.policy_id)
    ilog.record(p, request.user, f"Instalment due {i.due_date:%d %b %Y} removed")
    i.delete()
    p.sync_premium_due()
    return redirect("insurance_detail", pk=p.pk)


@manager
@require_POST
def quote_add(request, pk):
    p = _own_policy(request, pk)
    insurer = (request.POST.get("insurer") or "").strip()[:120]
    if not insurer or not p.current:
        messages.error(request, "Write the insurer that gave the quote." if p.current else "That policy cannot be renewed any more.")
        return redirect("insurance_detail", pk=p.pk)
    RenewalQuote.objects.create(policy=p, insurer=insurer, premium=_amount(request, "premium"),
                                sum_insured=_amount(request, "sum_insured"), valid_until=_parse_date(request.POST.get("valid_until")),
                                notes=(request.POST.get("notes") or "").strip()[:300], created_by=request.user)
    ilog.record(p, request.user, f"Renewal quote added from {insurer}")
    messages.success(request, "Quote added.")
    return redirect("insurance_detail", pk=p.pk)


@manager
@require_POST
def quote_choose(request, pk):
    q = get_object_or_404(RenewalQuote.objects.select_related("policy"), pk=pk)
    p = _own_policy(request, q.policy_id)
    if q.chosen:
        q.chosen = False
        q.save(update_fields=["chosen"])
        ilog.record(p, request.user, f"Quote from {q.insurer} is no longer the chosen one")
    else:
        p.quotes.filter(chosen=True).update(chosen=False)
        q.chosen = True
        q.save(update_fields=["chosen"])
        ilog.record(p, request.user, f"Quote from {q.insurer} chosen for the renewal")
    return redirect("insurance_detail", pk=p.pk)


@manager
@require_POST
def quote_delete(request, pk):
    q = get_object_or_404(RenewalQuote.objects.select_related("policy"), pk=pk)
    p = _own_policy(request, q.policy_id)
    ilog.record(p, request.user, f"Renewal quote from {q.insurer} removed")
    q.delete()
    return redirect("insurance_detail", pk=p.pk)


def _summary(p, today, cfg, instalments, claims, quotes):
    """The few things to know before reading the page: (text, tone) with tone bad, warn or ok."""
    if p.archived_at:
        return []
    out = []
    left = (p.expiry_date - today).days
    if p.renewed:
        out.append(("Renewed", "ok"))
    elif p.cancelled:
        out.append(("Cancelled", "bad"))
    elif left < 0:
        out.append((f"Expired {-left} day{'s' if left != -1 else ''} ago, not renewed", "bad"))
    elif left <= _soon(p, cfg):
        out.append((f"Expires in {left} day{'s' if left != 1 else ''}", "warn"))
    else:
        out.append((f"Expires {p.expiry_date:%d %b %Y} ({left} days)", "ok"))
    overdue = [i for i in instalments if i.state == "overdue"]
    if overdue:
        out.append((f"{len(overdue)} instalment{'s' if len(overdue) != 1 else ''} overdue", "bad"))
    elif p.premium_due and 0 <= (p.premium_due - today).days <= 30:
        d = (p.premium_due - today).days
        out.append((f"Premium due in {d} day{'s' if d != 1 else ''}" if d else "Premium due today", "warn"))
    open_claims = [c for c in claims if c.status in ("filed", "review", "approved")]
    if open_claims:
        out.append((f"{len(open_claims)} open claim{'s' if len(open_claims) != 1 else ''}", "warn"))
    if p.current and not quotes and cfg.insurance_quote_days and left <= cfg.insurance_quote_days:
        out.append(("No renewal quote yet", "warn"))
    return out


@viewer
def insurance_detail(request, pk):
    p = _get_visible(request.user, pk)
    cfg = SiteSettings.load()
    today = timezone.localdate()
    p.status = p.status_on(today, _soon(p, cfg))
    p.days_left = (p.expiry_date - today).days
    arch = None if request.user.can_manage_insurance else False
    terms = history.series_for(p, _scoped(request.user, archived=arch).prefetch_related("centres"), history.policy_series,
                               {"cover_type_id": p.cover_type_id})
    for t in terms:
        t.status = t.status_on(today, _soon(t, cfg))
    instalments = _instalment_rows(p, today)
    claims = list(p.claims.select_related("equipment").prefetch_related("documents"))
    quotes = list(p.quotes.all())
    summary = _summary(p, today, cfg, instalments, claims, quotes)
    return render(request, "insurance_detail.html", {
        "summary": summary, "past_terms": [t for t in terms if t.pk != p.pk], "p": p, "money": _show_money(request.user, cfg), "can_edit": request.user.can_manage_insurance and not p.archived_at,
        "can_restore": request.user.can_manage_insurance and bool(p.archived_at), "can_admin": request.user.is_admin,
        "docs": p.documents.all(), "machines": p.equipment.select_related("centre"), "soon": _soon(p, cfg),
        "premium_days": (p.premium_due - today).days if p.premium_due else None,
        "log": list(p.log.select_related("actor")[:40]), "instalments": instalments,
        "quotes": quotes, "quote_days": cfg.insurance_quote_days,
        "terms": INSTALMENT_TERMS,
        "claims": claims,
        "stages": insurance_stages(_soon(p, cfg)),
        "renewed_from": p.renewed_from, "renewals": p.renewals.filter(archived_at__isnull=True), "max_mb": MAX_FILE_MB})


@manager
def insurance_new(request):
    cfg = SiteSettings.load()
    money = _show_money(request.user, cfg)
    old = None
    renew = request.GET.get("renew") or request.POST.get("renew")
    if renew and renew.isdigit():
        old = get_object_or_404(InsurancePolicy, pk=int(renew), archived_at__isnull=True)
        if not old.current:
            later = old.renewals.first()
            messages.error(request, "That policy is " + ("already renewed" + (f" by policy {later.policy_number}" if later else "")
                                                         if old.renewed else "cancelled") + ", so it cannot be renewed again.")
            return redirect("insurance_detail", pk=old.pk)
    initial = {}
    chosen = old.quotes.filter(chosen=True).first() if old else None
    if old:
        start = old.expiry_date + timedelta(days=1)
        initial = {"centres": list(old.centres.all()), "cover_type": old.cover_type, "insurer": old.insurer,
                   "policy_number": old.policy_number, "contact": old.contact, "start_date": start,
                   "expiry_date": _plus_year(start), "sum_insured": old.sum_insured, "premium": old.premium,
                   "responsible": list(old.responsible.all()), "remind_days": old.remind_days,
                   "equipment": list(old.equipment.all()), "covers_all_machines": old.covers_all_machines,
                   "covers": old.covers,
                   "premium_due": _same_day_next_year(old.premium_due) if old.premium_due and old.premium_due >= old.start_date else None}
    if chosen:  # the quote picked for the renewal replaces the old insurer and amounts
        initial["insurer"] = chosen.insurer
        if money and chosen.premium is not None:
            initial["premium"] = chosen.premium
        if money and chosen.sum_insured is not None:
            initial["sum_insured"] = chosen.sum_insured
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
                ilog.record(p, request.user, f"Added as the renewal of policy {old.policy_number} "
                                             f"(ended {old.expiry_date:%d %b %Y}), with {len(files)} document{'s' if len(files) != 1 else ''}")
                ilog.record(old, request.user, f"Renewed by policy {p.policy_number} "
                                               f"({p.start_date:%d %b %Y} to {p.expiry_date:%d %b %Y})")
            else:
                ilog.record(p, request.user, f"Policy added, with {len(files)} document{'s' if len(files) != 1 else ''}")
            messages.success(request, "Policy saved." if not old else "Policy renewed. The old policy is marked Renewed.")
            if old and p.policy_number.strip().lower() == old.policy_number.strip().lower():
                messages.warning(request, "The policy number is the same as the old policy's. If the insurer issued a new "
                                          "number, edit the policy and change it.")
            if old and money and old.premium and p.premium and old.premium != p.premium:
                pct = (p.premium - old.premium) / old.premium * 100
                messages.info(request, f"The premium is {abs(pct):.0f}% {'higher' if pct > 0 else 'lower'} than the old policy's.")
            for w in form.warnings:
                messages.warning(request, w)
            return redirect("insurance_detail", pk=p.pk)
        messages.error(request, "The policy was not saved. Check the marked fields.")
    return render(request, "insurance_form.html", {"form": form, "old": old, "problems": problems, "money": money,
                                                   "max_mb": MAX_FILE_MB, "new": True, "chosen": chosen})


@manager
def insurance_edit(request, pk):
    p = get_object_or_404(InsurancePolicy, pk=pk, archived_at__isnull=True)
    money = _show_money(request.user)
    before = ilog.snapshot(p)  # taken first: validating the form changes the object in memory
    form = PolicyForm(request.POST or None, instance=p, show_money=money)
    files = request.FILES.getlist("files")
    problems = check_files(files) if request.method == "POST" else []
    if request.method == "POST":
        if form.is_valid() and not problems:
            form.save()
            p = InsurancePolicy.objects.get(pk=p.pk)
            ilog.record_changes(p, request.user, before)
            history.relink(p, form.cleaned_data.get("earlier"), request.user, InsurancePolicy, ilog.record)
            if files:
                ilog.record(p, request.user, f"Added {len(files)} document{'s' if len(files) != 1 else ''}")
            save_files(p, files, request.user)
            messages.success(request, "Policy saved.")
            for w in form.warnings:
                messages.warning(request, w)
            return redirect("insurance_detail", pk=p.pk)
        messages.error(request, "The policy was not saved. Check the marked fields.")
    return render(request, "insurance_form.html", {"form": form, "p": p, "problems": problems, "money": money,
                                                   "max_mb": MAX_FILE_MB, "new": False})


@manager
@require_POST
def insurance_archive(request, pk):
    """Keeps the policy, its documents, claims and history, but takes it out of the lists, reminders and checks."""
    p = get_object_or_404(_scoped(request.user), pk=pk)
    p.archived_at, p.archived_by = timezone.now(), request.user
    p.save(update_fields=["archived_at", "archived_by"])
    ilog.record(p, request.user, "Archived")
    old = p.renewed_from
    if old and old.renewed and not old.renewals.filter(archived_at__isnull=True).exists():
        # this was the renewal: the policy it replaced is the current one again
        InsurancePolicy.objects.filter(pk=old.pk).update(renewed=False)
        ilog.record(old, request.user, f"Current again: its renewal {p.policy_number} was archived")
    messages.success(request, "Policy archived. Find it again under Status: Archived.")
    return redirect("insurance_list")


@manager
@require_POST
def insurance_restore(request, pk):
    p = get_object_or_404(_scoped(request.user, archived=True), pk=pk)
    p.archived_at = p.archived_by = None
    p.save(update_fields=["archived_at", "archived_by"])
    ilog.record(p, request.user, "Restored from the archive")
    old = p.renewed_from
    if old and not old.archived_at and not old.renewed:
        InsurancePolicy.objects.filter(pk=old.pk).update(renewed=True)
        ilog.record(old, request.user, f"Renewed again: its renewal {p.policy_number} was restored")
    messages.success(request, "Policy restored.")
    return redirect("insurance_detail", pk=p.pk)


@admin_only
@require_POST
def insurance_purge(request, pk):
    """Deleting for good: only an administrator, only an archived policy, and only after typing its number.
    The history lines stay (with the policy's name) so there is still a record that it existed."""
    p = get_object_or_404(_scoped(request.user, archived=True), pk=pk)
    if (request.POST.get("confirm") or "").strip().lower() != p.policy_number.strip().lower():
        messages.error(request, "Type the policy number exactly to delete it for good. Nothing was deleted.")
        return redirect("insurance_detail", pk=p.pk)
    ilog.record(p, request.user, f"Deleted for good by {request.user.display_name}")
    for d in p.documents.all():
        d.file.delete(save=False)
    for d in ClaimDocument.objects.filter(claim__policy=p):
        d.file.delete(save=False)
    p.delete()
    messages.success(request, "Policy deleted for good. A line in the history still records it.")
    return redirect("insurance_list")


# --------------------------------------------------------------------------
# Documents
# --------------------------------------------------------------------------
@manager
@require_POST
def insurance_doc_add(request, pk):
    p = get_object_or_404(InsurancePolicy, pk=pk, archived_at__isnull=True)
    files = request.FILES.getlist("files")
    problems = check_files(files) if files else ["Choose a file first."]
    if problems:
        for x in problems:
            messages.error(request, x)
    else:
        save_files(p, files, request.user)
        ilog.record(p, request.user, f"Added {len(files)} document{'s' if len(files) != 1 else ''}: "
                                     + ", ".join(f.name for f in files))
        messages.success(request, f"{len(files)} file{'s' if len(files) != 1 else ''} added.")
    return redirect("insurance_detail", pk=p.pk)


@manager
@require_POST
def insurance_doc_delete(request, pk):
    d = get_object_or_404(InsuranceDocument, pk=pk, policy__archived_at__isnull=True)
    p = d.policy
    if p.documents.count() <= 1:
        messages.error(request, "A policy must keep at least one document. Add the new one first, then remove this one.")
    else:
        name = d.name
        d.file.delete(save=False)
        d.delete()
        ilog.record(p, request.user, f"Removed document {name}")
        messages.success(request, "Document removed.")
    return redirect("insurance_detail", pk=p.pk)


def serve_document(request, d):
    """Sends a stored policy or licence document: inline for PDFs and images, otherwise (or with ?download=1) as a download."""
    is_pdf = d.name.lower().endswith(".pdf")
    download = request.GET.get("download") == "1"
    try:
        resp = FileResponse(d.file.open("rb"), filename=d.name,
                            as_attachment=download or not (d.is_image or is_pdf))
    except FileNotFoundError:
        raise Http404
    resp = _safe(resp)
    if is_pdf and not download:
        # The in-app viewer shows the PDF in a frame of this site. The browser's PDF viewer does not start under the
        # strict "default-src 'none'" rule, so a PDF gets the narrower one: it may be framed by this site only.
        resp["Content-Security-Policy"] = "frame-ancestors 'self'"
        resp["X-Frame-Options"] = "SAMEORIGIN"
    return resp


@viewer
def insurance_doc(request, pk):
    d = get_object_or_404(InsuranceDocument.objects.select_related("policy"), pk=pk)
    if not _can_open(request.user, d.policy_id):
        raise Http404
    return serve_document(request, d)


@viewer
def claim_doc(request, pk):
    d = get_object_or_404(ClaimDocument.objects.select_related("claim"), pk=pk)
    if not _can_open(request.user, d.claim.policy_id):
        raise Http404
    return serve_document(request, d)


@manager
@require_POST
def claim_doc_delete(request, pk):
    d = get_object_or_404(ClaimDocument.objects.select_related("claim__policy"), pk=pk)
    p = d.claim.policy
    if not _scoped(request.user).filter(pk=p.pk).exists():
        raise Http404
    ilog.record(p, request.user, f"Removed document {d.name} from claim {_claim_line(d.claim)}")
    d.file.delete(save=False)
    d.delete()
    messages.success(request, "Document removed.")
    return redirect("claim_edit", pk=d.claim_id)


def home_card(user):
    """What the Home page shows to people with Insurance access: counts and the policies needing attention. None without access."""
    if not user.can_view_insurance:
        return None
    cfg = SiteSettings.load()
    today = timezone.localdate()
    rows = list(_scoped(user).filter(cancelled=False, renewed=False).annotate(doc_count=Count("documents", distinct=True)))
    expired, expiring, premiums = [], [], []
    for p in rows:
        p.days_left = (p.expiry_date - today).days
        p.status = p.status_on(today, _soon(p, cfg))
        if p.status == InsurancePolicy.EXPIRED:
            expired.append(p)
        elif p.status == InsurancePolicy.EXPIRING:
            expiring.append(p)
        if p.premium_due and (p.premium_due - today).days <= 14 and p.expiry_date >= today - timedelta(days=30):
            p.premium_days = (p.premium_due - today).days
            premiums.append(p)
    centres = Centre.objects.filter(active=True)
    if not user.insurance_sees_all:
        centres = centres.filter(pk__in=user.centres.all())
    # Most urgent first: premiums overdue or due, then expired, then those ending soonest
    items = ([{"p": p, "kind": "premium", "days": p.premium_days} for p in sorted(premiums, key=lambda x: x.premium_days)]
             + [{"p": p, "kind": "expired", "days": p.days_left} for p in sorted(expired, key=lambda x: -x.days_left)]
             + [{"p": p, "kind": "expiring", "days": p.days_left} for p in sorted(expiring, key=lambda x: x.days_left)])
    from .views_licence import _scoped as _lic_scoped
    lic_x, lic_e = [], []
    for x in _lic_scoped(user).filter(closed=False, renewed=False):
        st = x.status_on(today)
        x.days_left = (x.valid_until - today).days
        (lic_x if st == Licence.EXPIRED else lic_e if st == Licence.EXPIRING else []).append(x)
    lic_items = ([{"l": x, "kind": "expired", "days": x.days_left} for x in sorted(lic_x, key=lambda x: -x.days_left)]
                 + [{"l": x, "kind": "expiring", "days": x.days_left} for x in sorted(lic_e, key=lambda x: x.days_left)])
    return {"expired": len(expired), "expiring": len(expiring), "premiums": len(premiums),
            "lic_expired": len(lic_x), "lic_expiring": len(lic_e), "lic_items": lic_items[:4], "lic_more": max(0, len(lic_items) - 4),
            "no_document": sum(1 for p in rows if not p.doc_count), "gaps": _gap_count(user, centres, cfg),
            "uninsured": len(uninsured(user, centres, cfg)),
            "items": items[:6], "more": max(0, len(items) - 6), "soon_days": cfg.insurance_alert_days or 60}


def machine_policies(user, equipment):
    """The current policies that list this machine, for its page. None when the person may not see insurance."""
    if not user.can_view_insurance:
        return None
    cfg = SiteSettings.load()
    today = timezone.localdate()
    rows = list(_scoped(user).filter(cancelled=False, renewed=False).filter(
        Q(equipment=equipment) | Q(covers_all_machines=True) & (Q(centres=equipment.centre_id) | Q(centres__isnull=True))
    ).distinct())
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
ACCESS_FILTERS = [("with", "People with access"), ("all", "Everyone")]


def _access_rows(request):
    """Every active person with what they may do in Insurance, for the access audit and its Excel."""
    from .models import User
    cfg = SiteSettings.load()
    want = request.GET.get("show", "with")
    want = want if want in dict(ACCESS_FILTERS) else "with"
    resp_count = {}
    for p in InsurancePolicy.objects.filter(cancelled=False, renewed=False, archived_at__isnull=True).values_list("responsible", flat=True):
        if p:
            resp_count[p] = resp_count.get(p, 0) + 1
    rows = []
    for u in User.objects.filter(is_active=True).prefetch_related("centres").order_by("first_name", "username"):
        level = u.insurance_level
        if want == "with" and not level:
            continue
        sees_all = u.insurance_sees_all
        rows.append({
            "u": u, "level": level,
            "level_label": "Administrator (always full access)" if u.is_admin else dict(User.INSURANCE_ACCESS).get(level, "No access"),
            "scope": ("All centres" if sees_all else ", ".join(c.name for c in u.centres.all()) or "No centre set, so only policies for every centre")
                     if level else "",
            "can_edit": u.can_manage_insurance, "money": bool(level) and _show_money(u, cfg),
            "emails": bool(level) and bool(u.email),
            "no_email": bool(level) and not u.email,
            "responsible_for": resp_count.get(u.pk, 0),
        })
    return rows, want, cfg


@admin_only
def insurance_access(request):
    rows, want, cfg = _access_rows(request)
    return render(request, "insurance_access.html", {
        "rows": rows, "show": want, "filters": ACCESS_FILTERS, "money_on": cfg.insurance_show_money,
        "n_edit": sum(1 for r in rows if r["can_edit"]), "n_no_email": sum(1 for r in rows if r["no_email"]),
        "n_unscoped": sum(1 for r in rows if r["level"] == "own" and not r["u"].centres.exists()),
        "tab": "access"})


@admin_only
def insurance_access_export(request):
    from openpyxl import Workbook
    from openpyxl.styles import Font
    rows, _, _ = _access_rows(request)
    wb = Workbook()
    ws = wb.active
    ws.title = "Insurance access"
    ws.append(["Name", "User name", "Role", "Insurance access", "Sees", "Can add and edit", "Sees money", "Gets reminders by email",
               "Responsible for (current policies)"])
    for c in ws[1]:
        c.font = Font(bold=True)
    for r in rows:
        u = r["u"]
        ws.append([u.display_name, u.username, u.get_role_display(), r["level_label"], r["scope"],
                   "Yes" if r["can_edit"] else "No", "Yes" if r["money"] else "No",
                   "Yes" if r["emails"] else ("No email address" if r["no_email"] else ""), r["responsible_for"]])
    for i, w in enumerate([24, 18, 18, 36, 40, 16, 12, 22, 16], start=1):
        ws.column_dimensions[chr(64 + i)].width = w
    buf = io.BytesIO()
    wb.save(buf)
    resp = HttpResponse(buf.getvalue(), content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    resp["Content-Disposition"] = 'attachment; filename="insurance-access.xlsx"'
    return resp


ACTIVITY_KINDS = [("", "All kinds"), ("policy", "Policies"), ("licence", "Licences"), ("claim", "Claims"),
                  ("instalment", "Instalments"), ("quote", "Renewal quotes")]
_KIND_PREFIX = {"claim": "Claim", "instalment": "Instalment", "quote": ("Quote", "Renewal quote")}


def _kind_of(text):
    for k, pre in _KIND_PREFIX.items():
        if text.startswith(pre):
            return k
    return "policy"


def _activity_rows(request):
    """Everything logged on policies, licences, claims, instalments and quotes that this person may see, newest first.
    Entries about a record that was deleted for good are shown to administrators only: they no longer have centres."""
    from . import views_licence as vl
    from .models import InsuranceLog, LicenceLog
    user, g = request.user, request.GET
    f = {"kind": g.get("kind", "") if g.get("kind", "") in dict(ACTIVITY_KINDS) else "", "who": g.get("who", ""),
         "centre": g.get("centre", ""), "q": g.get("q", "").strip(), "start": g.get("start", ""), "end": g.get("end", "")}
    d1, d2 = _parse_date(f["start"]), _parse_date(f["end"])
    pol = InsuranceLog.objects.select_related("actor", "policy")
    lic = LicenceLog.objects.select_related("actor", "licence")
    pol = pol.filter(Q(policy__in=_scoped(user, archived=None)) | Q(policy__isnull=user.is_admin))
    lic = lic.filter(Q(licence__in=vl._scoped(user, archived=None)) | Q(licence__isnull=user.is_admin))
    for qs_name in ("pol", "lic"):
        qs = pol if qs_name == "pol" else lic
        link = "policy" if qs_name == "pol" else "licence"
        if d1:
            qs = qs.filter(at__date__gte=d1)
        if d2:
            qs = qs.filter(at__date__lte=d2)
        if f["who"] == "0":
            qs = qs.filter(actor__isnull=True)
        elif f["who"].isdigit():
            qs = qs.filter(actor_id=int(f["who"]))
        if f["centre"] == "0":
            qs = qs.filter(**{f"{link}__centres__isnull": True})
        elif f["centre"].isdigit():
            qs = qs.filter(Q(**{f"{link}__centres": int(f["centre"])}) | Q(**{f"{link}__centres__isnull": True}))
        if f["q"]:
            qs = qs.filter(Q(text__icontains=f["q"]) | Q(label__icontains=f["q"]))
        if qs_name == "pol":
            pol = qs.distinct()
        else:
            lic = qs.distinct()
    rows = []
    if f["kind"] in ("", "licence"):
        rows += [{"at": e.at, "who": e.actor.display_name if e.actor else "System", "kind": "licence", "label": e.label,
                  "obj": e.licence, "text": e.text, "gone": e.licence_id is None} for e in lic]
    if f["kind"] != "licence":
        for e in pol:
            k = _kind_of(e.text)
            if f["kind"] in ("", k):
                rows.append({"at": e.at, "who": e.actor.display_name if e.actor else "System", "kind": k, "label": e.label,
                             "obj": e.policy, "text": e.text, "gone": e.policy_id is None})
    rows.sort(key=lambda r: r["at"], reverse=True)
    return rows, f


@manager
def insurance_activity(request):
    rows, f = _activity_rows(request)
    page = Paginator(rows, 100).get_page(request.GET.get("page"))
    centres = Centre.objects.filter(active=True)
    if not request.user.insurance_sees_all:
        centres = centres.filter(pk__in=request.user.centres.all())
    from .models import InsuranceLog, LicenceLog
    actor_ids = set(InsuranceLog.objects.values_list("actor_id", flat=True)) | set(LicenceLog.objects.values_list("actor_id", flat=True))
    people = User.objects.filter(pk__in={a for a in actor_ids if a}).order_by("first_name", "username")
    q = request.GET.copy()
    q.pop("page", None)
    return render(request, "insurance_activity.html", {
        "page": page, "f": f, "kinds": ACTIVITY_KINDS, "centres": centres, "people": people, "tab": "activity",
        "total": len(rows), "query": q.urlencode()})


@manager
def insurance_activity_export(request):
    from openpyxl import Workbook
    from openpyxl.styles import Font
    rows, _ = _activity_rows(request)
    wb = Workbook()
    ws = wb.active
    ws.title = "Activity"
    ws.append(["When", "Who", "Kind", "Record", "What happened", "Record still exists"])
    for c in ws[1]:
        c.font = Font(bold=True)
    kinds = dict(ACTIVITY_KINDS)
    for r in rows[:50000]:
        ws.append([timezone.localtime(r["at"]).replace(tzinfo=None), r["who"], kinds[r["kind"]], r["label"], r["text"],
                   "No, deleted for good" if r["gone"] else "Yes"])
    for cell in ws["A"][1:]:
        cell.number_format = "dd mmm yyyy hh:mm"
    for i, w in enumerate([18, 22, 16, 50, 60, 18], start=1):
        ws.column_dimensions[chr(64 + i)].width = w
    buf = io.BytesIO()
    wb.save(buf)
    resp = HttpResponse(buf.getvalue(), content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    resp["Content-Disposition"] = 'attachment; filename="insurance-activity.xlsx"'
    return resp


def _history_data(request):
    """(kind, series list, money) for the History tab, limited to what the person may see. Archived records are
    included for people who may restore them."""
    from . import views_licence as vl
    user, cfg = request.user, SiteSettings.load()
    today = timezone.localdate()
    kind = "licences" if request.GET.get("kind") == "licences" else "insurance"
    q = request.GET.get("q", "").strip()
    centre = request.GET.get("centre", "")
    arch = None if user.can_manage_insurance else False
    out = []
    if kind == "insurance":
        qs = _scoped(user, archived=arch).select_related("cover_type", "renewed_from").prefetch_related("centres")
        for terms in history.policy_series(qs):
            for p in terms:
                p.status = p.status_on(today, _soon(p, cfg))
            out.append({"title": terms[0].cover_type.name, "where": terms[0].location, "terms": terms,
                        "centre_ids": {c.pk for t in terms for c in t.centres.all()}})
    else:
        qs = vl._scoped(user, archived=arch).select_related("renewed_from").prefetch_related("centres")
        for terms in history.licence_series(qs):
            for x in terms:
                x.status = x.status_on(today)
            out.append({"title": terms[-1].name, "where": terms[-1].location, "terms": terms,
                        "centre_ids": {c.pk for t in terms for c in t.centres.all()}})
    if q:
        ql = q.lower()
        out = [s for s in out if ql in s["title"].lower() or any(
            ql in (getattr(t, "insurer", "") or getattr(t, "issuer", "") or "").lower()
            or ql in (getattr(t, "policy_number", "") or getattr(t, "reference_number", "") or "").lower() for t in s["terms"])]
    if centre == "0":
        out = [s for s in out if not s["centre_ids"]]
    elif centre.isdigit():
        out = [s for s in out if int(centre) in s["centre_ids"] or not s["centre_ids"]]
    for s in out:
        s["count"] = len(s["terms"])
        s["warnings"] = sum(1 for t in s["terms"] if t.gap)
        s["first"], s["last"] = s["terms"][0], s["terms"][-1]
    out.sort(key=lambda s: (s["title"].lower(), s["where"]))
    return kind, out, _show_money(user, cfg), {"q": q, "centre": centre}


@viewer
def insurance_history(request):
    kind, series, money, f = _history_data(request)
    centres = Centre.objects.filter(active=True)
    if not request.user.insurance_sees_all:
        centres = centres.filter(pk__in=request.user.centres.all())
    return render(request, "insurance_history.html", {
        "kind": kind, "series": series, "money": money, "f": f, "centres": centres, "tab": "history",
        "terms_total": sum(s["count"] for s in series), "warn_total": sum(s["warnings"] for s in series)})


@viewer
def insurance_history_export(request):
    from openpyxl import Workbook
    from openpyxl.styles import Font
    kind, series, money, _ = _history_data(request)
    ins = kind == "insurance"
    wb = Workbook()
    ws = wb.active
    ws.title = "Insurance history" if ins else "Licence history"
    head = ["Cover type" if ins else "Licence", "Centres", "Term", "Start date" if ins else "Issued", "End date" if ins else "Valid until",
            "Insurer" if ins else "Issuer", "Policy number" if ins else "Reference number", "Status", "Archived",
            "Renews number", "Note"]
    if ins and money:
        head += ["Sum insured", "Premium"]
    ws.append(head)
    for c in ws[1]:
        c.font = Font(bold=True)
    labels = dict(InsurancePolicy.STATUS_LABELS if ins else Licence.STATUS_LABELS)
    for s in series:
        for i, t in enumerate(s["terms"], start=1):
            start, end = (t.start_date, t.expiry_date) if ins else (t.issued_on, t.valid_until)
            num = t.policy_number if ins else t.reference_number
            prev = t.renewed_from
            line = [s["title"], s["where"], i, start, end, t.insurer if ins else t.issuer, num,
                    labels.get(t.status, t.status),
                    t.archived_at.strftime("%d %b %Y") if t.archived_at else "",
                    (prev.policy_number if ins else prev.reference_number) if prev else "", t.gap]
            if ins and money:
                line += [float(t.sum_insured) if t.sum_insured is not None else None,
                         float(t.premium) if t.premium is not None else None]
            ws.append(line)
    for col in ("D", "E"):
        for cell in ws[col][1:]:
            cell.number_format = "dd mmm yyyy"
    for i, w in enumerate([26, 26, 7, 14, 14, 24, 20, 14, 14, 18, 44, 14, 14][:len(head)], start=1):
        ws.column_dimensions[chr(64 + i)].width = w
    buf = io.BytesIO()
    wb.save(buf)
    resp = HttpResponse(buf.getvalue(), content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    resp["Content-Disposition"] = f'attachment; filename="{"insurance" if ins else "licence"}-history.xlsx"'
    return resp


@viewer
def insurance_print(request):
    """The register laid out for paper or Save as PDF: policies (with the list's filters), licences, open claims and,
    where the check is on, valuable machines with no cover. Everything is limited to what the person may see."""
    from . import views_licence as vl
    user = request.user
    cfg = SiteSettings.load()
    today = timezone.localdate()
    money = _show_money(user, cfg)
    rows, f = _filtered(request)
    live = [p for p in rows if p.current]
    licences = []
    for x in vl._scoped(user).filter(closed=False, renewed=False).prefetch_related("centres").order_by("valid_until", "name"):
        x.status = x.status_on(today)
        x.days_left = (x.valid_until - today).days
        licences.append(x)
    claims = (InsuranceClaim.objects.filter(policy__in=_scoped(user), status__in=InsuranceClaim.OPEN)
              .select_related("policy", "policy__cover_type", "equipment").order_by("incident_date"))
    centres = Centre.objects.filter(active=True)
    if not user.insurance_sees_all:
        centres = centres.filter(pk__in=user.centres.all())
    return render(request, "insurance_print.html", {
        "rows": rows, "f": f, "money": money, "licences": licences, "claims": claims, "today": today,
        "uninsured": uninsured(user, centres, cfg), "min_value": cfg.insurance_min_value,
        "n_current": len(live), "n_expiring": sum(1 for p in live if p.status == InsurancePolicy.EXPIRING),
        "n_expired": sum(1 for p in live if p.status == InsurancePolicy.EXPIRED),
        "total_sum": sum((p.sum_insured or 0) for p in live) if money else None,
        "total_premium": sum((p.premium or 0) for p in live) if money else None,
        "status_label": dict(STATUS_FILTERS).get(f["status"], "Current policies"),
        "scope": "all centres" if user.insurance_sees_all else "your centres and the policies that cover every centre"})


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
            "Days left", "Status", "Responsible", "Machines covered", "Covers", "Next premium due", "Covers every machine"]
    if money:
        head += ["Sum insured", "Premium"]
    ws.append(head)
    for c in ws[1]:
        c.font = Font(bold=True)
    for p in rows:
        line = [p.location, p.cover_type.name, p.insurer, p.policy_number,
                p.contact, p.start_date, p.expiry_date, p.days_left, InsurancePolicy.STATUS_LABELS[p.status],
                ", ".join(u.display_name for u in p.responsible.all()),
                ", ".join(e.name for e in p.equipment.all()), p.covers, p.premium_due,
                "Yes" if p.covers_all_machines else "No"]
        if money:
            line += [float(p.sum_insured) if p.sum_insured is not None else None,
                     float(p.premium) if p.premium is not None else None]
        ws.append(line)
    for col in ("F", "G", "M"):
        for cell in ws[col][1:]:
            cell.number_format = "dd mmm yyyy"
    for i, w in enumerate([22, 24, 24, 20, 24, 14, 14, 10, 14, 20, 40, 30, 14, 16, 14][:len(head)], start=1):
        ws.column_dimensions[chr(64 + i)].width = w
    buf = io.BytesIO()
    wb.save(buf)
    resp = HttpResponse(buf.getvalue(), content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    resp["Content-Disposition"] = f'attachment; filename="insurance-{date.today():%Y-%m-%d}.xlsx"'
    return resp
