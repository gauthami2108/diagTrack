import re
import csv
import json
from datetime import datetime, timedelta
from functools import wraps

from django import forms
from django.conf import settings as dj_settings
from django.contrib import messages
from django.contrib.auth import views as auth_views
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.core.paginator import Paginator
from django.db.models import Count, Q
from django.http import FileResponse, Http404, HttpResponse, JsonResponse, StreamingHttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from . import notify, reports, services, wording
from .models import (HOLD_REASONS, OPEN_STATUSES, Category, CategoryField, Centre, Department, DowntimeLog, Equipment,
                     EscalationRule, Heartbeat, Notification, OutboundMessage, PreventiveMaintenance, Priority,
                     SignInLock, SiteSettings, SlaPolicy, Status, Ticket, MessageTemplate, TicketAttachment,
                     TicketComment, TicketHistory, User, Vendor, VendorCall)


def role_required(test):
    def deco(view):
        @wraps(view)
        @login_required
        def inner(request, *args, **kwargs):
            if not test(request.user):
                raise PermissionDenied
            return view(request, *args, **kwargs)
        return inner
    return deco


not_requester = role_required(lambda u: u.role != User.REQUESTER or u.is_superuser)
admin_only = role_required(lambda u: u.is_admin)


# --------------------------------------------------------------------------
# Home
# --------------------------------------------------------------------------
@login_required
def home(request):
    user = request.user
    visible = services.visible_tickets(user)
    now = timezone.now()
    mine_open = visible.filter(requester=user, status__in=OPEN_STATUSES)
    to_confirm = visible.filter(requester=user, status=Status.RESOLVED)
    work = visible.filter(assignee=user, status__in=OPEN_STATUSES).order_by("resolution_due")
    ctx = {
        "head": reports.headline(visible, now),
        "mine_open": mine_open[:6],
        "to_confirm": to_confirm[:5],
        "work": work[:8],
        "work_count": work.count(),
        "unassigned": visible.filter(status__in=OPEN_STATUSES, assignee__isnull=True).order_by("resolution_due")[:6]
        if user.role in (User.DEPT_HEAD, User.ADMIN) or user.is_superuser else [],
        "at_risk": visible.filter(status__in=[Status.NEW, Status.ASSIGNED, Status.IN_PROGRESS])
        .filter(Q(resolution_due__lt=now + timedelta(hours=4))).order_by("resolution_due")[:6]
        if user.role != User.REQUESTER else [],
        "recent": visible.order_by("-updated_at")[:6],
        "greeting": "Good morning" if timezone.localtime().hour < 12 else
                    "Good afternoon" if timezone.localtime().hour < 17 else "Good evening",
        "mine_open_count": mine_open.count(),
    }
    return render(request, "home.html", ctx)


# --------------------------------------------------------------------------
# Raise a ticket
# --------------------------------------------------------------------------
def _raise_context(user, equipment=None):
    centres = list(user.centres.filter(active=True)) or list(Centre.objects.filter(active=True))
    if equipment is not None and equipment.centre not in centres:
        centres.append(equipment.centre)  # scanned a machine at another centre
    cats = Category.objects.filter(active=True, department__active=True).select_related("department").prefetch_related("fields")
    meta = {str(c.pk): {
        "department": c.department.name, "priority": c.get_default_priority_display(),
        "equipment": c.asks_equipment, "describe": c.asks_description,
        "fields": [{"id": f.pk, "label": f.label, "kind": f.kind, "choices": f.choice_list(), "required": f.required}
                   for f in c.fields.all()],
    } for c in cats}
    equipment = {}
    for e in Equipment.objects.filter(active=True, centre__in=centres):
        equipment.setdefault(str(e.centre_id), []).append({"id": e.pk, "name": e.name})
    grouped = {}
    for c in cats:
        grouped.setdefault(c.department.name, []).append(c)
    return {"centres": centres, "grouped": grouped, "meta": meta, "equipment_map": equipment,
            "posted_extra": {}}


@login_required
def ticket_new(request):
    user = request.user
    if user.role == User.MANAGEMENT and not user.is_superuser:
        raise PermissionDenied
    scanned = None
    if (request.GET.get("equipment") or "").isdigit():
        scanned = Equipment.objects.filter(pk=int(request.GET["equipment"]), active=True).select_related("centre").first()
    ctx = _raise_context(user, scanned)
    if scanned and request.method != "POST":
        word = scanned.name.split()[0].lower()
        cats = Category.objects.filter(active=True, asks_equipment=True)
        guess = next((c for c in cats if c.name.lower().startswith(word)), None) or cats.first()
        ctx["posted"] = {"centre": str(scanned.centre_id), "equipment": str(scanned.pk),
                         "category": str(guess.pk) if guess else ""}
        ctx["scanned"] = scanned
    if request.method == "POST":
        p = request.POST
        errors = []
        centre = next((c for c in ctx["centres"] if str(c.pk) == p.get("centre")), None)
        category = Category.objects.filter(pk=p.get("category") or 0, active=True).first()
        subject, description = p.get("subject", "").strip(), p.get("description", "").strip()
        if not centre:
            errors.append("Choose the centre.")
        if not category:
            errors.append("Choose what the issue is about.")
        if not subject:
            errors.append("Write a short title.")
        if not description and not (category and not category.asks_description):
            errors.append("Describe what happened.")
        equipment, extra = None, {}
        if category and centre:
            if category.asks_equipment and p.get("equipment"):
                equipment = Equipment.objects.filter(pk=p["equipment"], centre=centre, active=True).first()
            for f in category.fields.all():
                value = p.get(f"field_{f.pk}", "").strip()
                if f.required and not value:
                    errors.append(f"Fill in: {f.label}.")
                if value:
                    extra[f.label] = value
            if not category.asks_description:
                # No free-text box for this category: the answers to its own questions are the description.
                description = "\n".join(f"{k}: {v}" for k, v in extra.items()) or category.name
        if not errors:
            t = services.create_ticket(
                user, centre, category, subject, description, equipment=equipment,
                patient_impact=p.get("patient_impact") == "on",
                equipment_down=p.get("equipment_down") == "on", extra=extra,
                files=request.FILES.getlist("files"))
            who = t.assignee.display_name if t.assignee else f"the {t.department.name} team"
            messages.success(request, f"Ticket {t.number} raised and sent to {who}.")
            for name in t.skipped_files:
                messages.warning(request, f"{name} was too large and was not attached.")
            return redirect("ticket_detail", number=t.number)
        ctx.update(errors=errors, posted=p,
                   posted_extra={k: v for k, v in p.items() if k.startswith("field_")})
    return render(request, "ticket_form.html", ctx)


# --------------------------------------------------------------------------
# Lists
# --------------------------------------------------------------------------
VIEWS = {"mine": "Raised by me", "work": "Assigned to me", "queue": "Team queue", "all": "All tickets"}
SORTS = {"due": "resolution_due", "new": "-created_at", "updated": "-updated_at", "priority": "priority"}


def _scope(user, view):
    qs = services.visible_tickets(user)
    if view == "mine":
        return qs.filter(requester=user)
    if view == "work":
        return qs.filter(assignee=user)
    if view == "queue":
        if user.is_admin:
            return qs
        return qs.filter(Q(department__in=user.departments.all()) | Q(department__head=user))
    return qs


def _default_view(user):
    if user.role in (User.AGENT,):
        return "work"
    if user.role == User.DEPT_HEAD:
        return "queue"
    if user.role == User.REQUESTER:
        return "mine"
    return "all"


def _filtered(request, default_open=True):
    user, g = request.user, request.GET
    view = g.get("view") if g.get("view") in VIEWS else _default_view(user)
    qs = _scope(user, view)
    state = g.get("state", "open" if default_open else "any")
    if g.get("status"):
        qs = qs.filter(status=g["status"])
    elif state == "open":
        qs = qs.filter(status__in=OPEN_STATUSES)
    elif state == "done":
        qs = qs.exclude(status__in=OPEN_STATUSES)
    now = timezone.now()
    if g.get("priority"):
        qs = qs.filter(priority=g["priority"])
    for key in ("centre", "department", "category", "equipment"):
        if g.get(key, "").isdigit():
            qs = qs.filter(**{f"{key}_id": int(g[key])})
    if g.get("sla") == "breached":
        qs = qs.filter(Q(resolution_breached=True) | Q(resolution_due__lt=now, status__in=OPEN_STATUSES)).exclude(
            status=Status.ON_HOLD)
    elif g.get("sla") == "soon":
        qs = qs.filter(status__in=[Status.NEW, Status.ASSIGNED, Status.IN_PROGRESS],
                       resolution_due__gte=now, resolution_due__lt=now + timedelta(hours=4))
    if g.get("unassigned"):
        qs = qs.filter(assignee__isnull=True)
    for key, lookup in (("from", "created_at__date__gte"), ("to", "created_at__date__lte"),
                        ("closed_from", "closed_at__date__gte")):
        try:
            if g.get(key):
                qs = qs.filter(**{lookup: datetime.strptime(g[key], "%Y-%m-%d").date()})
        except ValueError:
            pass
    q = g.get("q", "").strip()
    if q:
        qs = qs.filter(Q(number__icontains=q) | Q(subject__icontains=q) | Q(description__icontains=q)
                       | Q(requester__first_name__icontains=q) | Q(requester__last_name__icontains=q)
                       | Q(equipment__name__icontains=q))
    sort = g.get("sort") if g.get("sort") in SORTS else ("due" if state == "open" and not g.get("status") else "new")
    return qs.order_by(SORTS[sort], "-id"), {"view": view, "state": state, "sort": sort, "q": q}


def _filter_options(user):
    views = dict(VIEWS)
    if not user.is_resolver:
        views.pop("work"), views.pop("queue")
    if user.role == User.REQUESTER and not user.is_superuser:
        views.pop("all")
    if user.role == User.MANAGEMENT and not user.is_superuser:
        views.pop("mine")
    return {"views": views, "statuses": Status.choices, "priorities": Priority.choices,
            "centres": Centre.objects.filter(active=True), "departments": Department.objects.filter(active=True)}


@login_required
def ticket_list(request):
    q = request.GET.get("q", "").strip().upper()
    if q:
        exact = services.visible_tickets(request.user).filter(number=q).first()
        if exact:
            return redirect("ticket_detail", number=exact.number)
    qs, state = _filtered(request)
    page = Paginator(qs, 25).get_page(request.GET.get("page"))
    ctx = {"page": page, "total": page.paginator.count, **state, **_filter_options(request.user),
           "title": VIEWS[state["view"]]}
    return render(request, "ticket_list.html", ctx)


@login_required
def export_csv(request):
    qs, _ = _filtered(request)
    resp = HttpResponse(content_type="text/csv; charset=utf-8")
    resp["Content-Disposition"] = f'attachment; filename="diagtrack-tickets-{timezone.localdate()}.csv"'
    resp.write("﻿")  # so Excel opens it correctly
    w = csv.writer(resp)
    w.writerow(["Ticket", "Raised", "Centre", "Department", "Category", "Title", "Priority", "Status",
                "Raised by", "Assigned to", "Equipment", "Resolve by", "Resolved", "Response missed",
                "Resolution missed", "Rating"])
    fmt = lambda d: timezone.localtime(d).strftime("%Y-%m-%d %H:%M") if d else ""
    for t in qs[:20000]:
        w.writerow([t.number, fmt(t.created_at), t.centre.name, t.department.name, t.category.name, t.subject,
                    t.priority, t.get_status_display(), t.requester.display_name,
                    t.assignee.display_name if t.assignee else "", t.equipment.name if t.equipment else "",
                    fmt(t.resolution_due), fmt(t.resolved_at), "Yes" if t.response_breached else "No",
                    "Yes" if t.resolution_breached else "No", t.rating or ""])
    return resp


@not_requester
def board(request):
    user = request.user
    qs = _scope(user, "queue" if user.is_resolver else "all")
    for key in ("centre", "department"):
        if request.GET.get(key, "").isdigit():
            qs = qs.filter(**{f"{key}_id": int(request.GET[key])})
    if request.GET.get("mine"):
        qs = qs.filter(assignee=user)
    recent = timezone.now() - timedelta(days=3)
    columns = [
        {"key": "todo", "title": "To do", "hint": "New and assigned", "accepts": "",
         "tickets": qs.filter(status__in=[Status.NEW, Status.ASSIGNED]).order_by("resolution_due")[:60]},
        {"key": "in_progress", "title": "In progress", "hint": "", "accepts": "start resume",
         "tickets": qs.filter(status=Status.IN_PROGRESS).order_by("resolution_due")[:60]},
        {"key": "on_hold", "title": "On hold", "hint": "Clock paused", "accepts": "hold",
         "tickets": qs.filter(status=Status.ON_HOLD).order_by("-updated_at")[:60]},
        {"key": "resolved", "title": "Resolved", "hint": "Last 3 days", "accepts": "resolve",
         "tickets": qs.filter(status=Status.RESOLVED, resolved_at__gte=recent).order_by("-resolved_at")[:60]},
    ]
    for col in columns:
        col["tickets"] = list(col["tickets"])
        for t in col["tickets"]:
            t.can = " ".join(sorted(services.allowed_actions(user, t) & {"start", "resume", "hold", "resolve"}))
    return render(request, "board.html", {"columns": columns, "hold_reasons": HOLD_REASONS,
                                          **_filter_options(user)})


# --------------------------------------------------------------------------
# One ticket
# --------------------------------------------------------------------------
def _when(value):
    """A date and time typed into a form, or None for 'now'."""
    if not value:
        return None
    try:
        return timezone.make_aware(datetime.strptime(value, "%Y-%m-%dT%H:%M"))
    except ValueError:
        raise services.NotAllowed("Enter a valid date and time.")


def _get_ticket(user, number):
    return get_object_or_404(services.visible_tickets(user), number=number.upper())


@login_required
def ticket_detail(request, number):
    user = request.user
    t = _get_ticket(user, number)
    can = services.allowed_actions(user, t)
    show_internal = "internal_note" in can or user.sees_everything
    timeline = [{"kind": "event", "at": h.created_at, "o": h} for h in t.history.select_related("actor")]
    for c in t.comments.select_related("author").prefetch_related("attachments"):
        if c.internal and not show_internal:
            continue
        timeline.append({"kind": "comment", "at": c.created_at, "o": c})
    timeline.sort(key=lambda x: x["at"])
    Notification.objects.filter(user=user, ticket=t, read=False).update(read=True)
    now = timezone.now()
    for item in timeline:
        if item["kind"] == "comment":
            c = item["o"]
            c.can_edit = c.author_id == user.pk and now - c.created_at <= services.EDIT_WINDOW
    downtime = getattr(t, "downtime", None)
    agents = services.candidate_agents(t.department, t.centre) if "assign" in can else []
    if "assign" in can and t.department.head and t.department.head not in agents:
        agents.append(t.department.head)
    ctx = {
        "t": t, "can": can, "timeline": timeline, "downtime": downtime,
        "files": t.attachments.filter(comment__isnull=True),
        "agents": agents, "hold_reasons": HOLD_REASONS, "priorities": Priority.choices,
        "departments": Department.objects.filter(active=True).exclude(pk=t.department_id),
        "now_local": timezone.localtime().strftime("%Y-%m-%dT%H:%M"),
        "response_state": ("met" if t.first_response_at and not t.response_breached else
                           "missed" if t.response_breached else "running"),
        "comment_count": sum(1 for i in timeline if i["kind"] == "comment"),
        "history_count": sum(1 for i in timeline if i["kind"] == "event"),
        "primary": next((a for a in ("start", "resume", "resolve", "close", "reopen") if a in can), ""),
        "vendor_calls": t.vendor_calls.select_related("vendor", "logged_by") if t.equipment_id else [],
        "can_vendor": services.can_log_vendor(user, t) and t.is_open,
        "vendors": Vendor.objects.all() if t.equipment_id else [],
    }
    if request.GET.get("preview"):
        return render(request, "_ticket_preview.html", ctx)
    return render(request, "ticket_detail.html", ctx)


@login_required
@require_POST
def ticket_action(request, number, action):
    user, p = request.user, request.POST
    t = _get_ticket(user, number)
    ok = None
    try:
        if action == "start":
            services.start_work(t, user)
            ok = "Work started."
        elif action == "assign_me":
            if "assign_me" not in services.allowed_actions(user, t):
                raise services.NotAllowed("You cannot take this ticket.")
            services.assign(t, user, user, check=False)
            ok = "The ticket is now assigned to you."
        elif action == "vendor_call":
            vendor = get_object_or_404(Vendor, pk=p.get("vendor") or 0)
            services.log_vendor_call(t, user, vendor, p.get("reference", ""), _when(p.get("called_at")),
                                     p.get("note", ""))
            ok = f"Call to {vendor.name} logged."
        elif action == "vendor_attended":
            call = get_object_or_404(VendorCall, pk=p.get("call") or 0, ticket=t)
            services.vendor_attended(call, user, p.get("engineer", ""), _when(p.get("attended_at")), p.get("note", ""))
            ok = "Engineer visit recorded."
        elif action == "edit_comment":
            c = get_object_or_404(TicketComment, pk=p.get("comment") or 0, ticket=t)
            services.edit_comment(c, user, p.get("body", ""))
            ok = "Comment updated."
        elif action == "assign":
            agent = get_object_or_404(User, pk=p.get("assignee") or 0, is_active=True)
            services.assign(t, user, agent)
            ok = f"Assigned to {agent.display_name}."
        elif action == "hold":
            services.hold(t, user, p.get("reason", ""), p.get("note", ""))
            ok = "Ticket is on hold. The SLA clock is paused."
        elif action == "resume":
            services.resume(t, user)
            ok = "Work resumed."
        elif action == "resolve":
            end = None
            if p.get("downtime_end"):
                try:
                    end = timezone.make_aware(datetime.strptime(p["downtime_end"], "%Y-%m-%dT%H:%M"))
                except ValueError:
                    raise services.NotAllowed("Enter the time the equipment came back.")
            services.resolve(t, user, p.get("note", ""), end)
            ok = "Ticket resolved. The requester has been asked to confirm."
        elif action == "close":
            services.close(t, user, p.get("rating") or None)
            ok = "Ticket closed."
        elif action == "reopen":
            services.reopen(t, user, p.get("note", ""))
            ok = "Ticket reopened."
        elif action == "cancel":
            services.cancel(t, user, p.get("note", ""))
            ok = "Ticket cancelled."
        elif action == "transfer":
            dept = get_object_or_404(Department, pk=p.get("department") or 0, active=True)
            services.transfer(t, user, dept, p.get("note", ""))
            ok = f"Transferred to {dept.name}."
        elif action == "priority":
            services.change_priority(t, user, p.get("priority", ""), p.get("note", ""))
            ok = "Priority changed."
        elif action == "comment":
            _, skipped = services.add_comment(t, user, p.get("body", ""), p.get("internal") == "on",
                                              request.FILES.getlist("files"))
            ok = "Note added." if p.get("internal") == "on" else "Reply added."
            for name in skipped:
                messages.warning(request, f"{name} was too large and was not attached.")
        else:
            raise Http404
    except services.NotAllowed as exc:
        messages.error(request, str(exc))
    else:
        messages.success(request, ok)
    if p.get("next") == "board":
        return redirect("board")
    if not services.visible_tickets(user).filter(pk=t.pk).exists():
        return redirect("ticket_list")  # for example after transferring it away
    return redirect("ticket_detail", number=t.number)


@login_required
def attachment(request, pk):
    a = get_object_or_404(TicketAttachment, pk=pk)
    if not services.visible_tickets(request.user).filter(pk=a.ticket_id).exists():
        raise Http404
    if a.comment and a.comment.internal and "internal_note" not in services.allowed_actions(request.user, a.ticket) \
            and not request.user.sees_everything:
        raise Http404
    try:
        return _safe(FileResponse(a.file.open("rb"), filename=a.name, as_attachment=not a.is_image
                                  and not a.name.lower().endswith(".pdf")))
    except FileNotFoundError:
        raise Http404


# --------------------------------------------------------------------------
# Equipment
# --------------------------------------------------------------------------
@not_requester
def equipment_list(request):
    user = request.user
    qs = Equipment.objects.filter(active=True).select_related("centre", "vendor")
    if not user.sees_everything and user.centres.exists():
        qs = qs.filter(centre__in=user.centres.all())
    if request.GET.get("centre", "").isdigit():
        qs = qs.filter(centre_id=int(request.GET["centre"]))
    q = request.GET.get("q", "").strip()
    if q:
        qs = qs.filter(Q(name__icontains=q) | Q(make__icontains=q) | Q(serial_no__icontains=q))
    items = list(qs)
    for e in items:
        e.stats = reports.equipment_uptime(e)
        e.open_tickets = e.tickets.filter(status__in=OPEN_STATUSES).count()
    scope = None if user.sees_everything or not user.centres.exists() else user.centres.all()
    tab = request.GET.get("tab") if request.GET.get("tab") in ("faults", "vendors", "amc", "pm", "penalties") else "machines"
    visible = services.visible_tickets(user)
    faults = reports.repeat_faults(visible)
    pm_list = reports.pm_schedules_due(scope)
    penalty_days = int(request.GET["days"]) if request.GET.get("days") in ("30", "90", "180", "365") else 90
    penalties = reports.camc_uptime_penalties(centres=scope, days=penalty_days)
    return render(request, "equipment_list.html", {
        "items": items, "centres": Centre.objects.filter(active=True), "q": q, "tab": tab,
        "down_count": sum(1 for e in items if e.stats["is_down"]),
        "faults": faults, "fault_ids": {f["equipment"].pk for f in faults},
        "vendors": reports.vendor_performance(centres=scope),
        "amc": reports.amc_due(scope, max(SiteSettings.load().amc_alert_days, 30)),
        "pm_list": pm_list,
        "pm_due_count": sum(1 for p in pm_list if p.due_status in ("overdue", "due_soon")),
        "penalties": penalties,
        "penalty_days": penalty_days})


@not_requester
def equipment_detail(request, pk):
    e = get_object_or_404(Equipment.objects.select_related("centre", "vendor"), pk=pk)
    tickets = services.visible_tickets(request.user).filter(equipment=e).order_by("-created_at")[:50]
    today = timezone.localdate()
    try:
        pm_schedules = list(e.pm_schedules.filter(active=True).select_related("assigned_to", "last_ticket").order_by("next_due_date"))
        for pm in pm_schedules:
            pm.days_left = (pm.next_due_date - today).days
            pm.days_overdue = abs(pm.days_left) if pm.days_left < 0 else 0
            pm.due_status = "overdue" if pm.days_left < 0 else "due_soon" if pm.days_left <= pm.advance_notice_days else "scheduled"
    except Exception:
        pm_schedules = []

    try:
        penalties_data = reports.camc_uptime_penalties(equipment=e, days=90)
        e_penalty = penalties_data["rows"][0] if penalties_data.get("rows") else None
    except Exception:
        e_penalty = None

    agents = list(User.objects.filter(role__in=[User.AGENT, User.DEPT_HEAD], is_active=True))

    return render(request, "equipment_detail.html", {
        "e": e, "stats": reports.equipment_uptime(e), "stats90": reports.equipment_uptime(e, 90),
        "calls": VendorCall.objects.filter(ticket__equipment=e).select_related("vendor", "ticket")[:20],
        "amc_left": (e.amc_expiry - timezone.localdate()).days if e.amc_expiry else None,
        "tickets": tickets, "downtime": DowntimeLog.objects.filter(equipment=e).select_related("ticket")[:30],
        "qr_target": _qr_target(request, e),
        "pm_schedules": pm_schedules,
        "penalty_stats": e_penalty,
        "agents": agents})


@not_requester
@require_POST
def equipment_pm_create(request, pk):
    e = get_object_or_404(Equipment, pk=pk)
    title = request.POST.get("title", "").strip()
    freq = int(request.POST.get("frequency_months") or 3)
    due_str = request.POST.get("next_due_date", "").strip()
    notice = int(request.POST.get("advance_notice_days") or 7)
    assigned_to_id = request.POST.get("assigned_to") or None
    notes = request.POST.get("notes", "").strip()
    if not title:
        messages.error(request, "Enter a title for the PM schedule.")
        return redirect("equipment_detail", pk=e.pk)
    try:
        next_due = datetime.strptime(due_str, "%Y-%m-%d").date()
    except ValueError:
        messages.error(request, "Enter a valid target due date.")
        return redirect("equipment_detail", pk=e.pk)

    assigned_to = User.objects.filter(pk=assigned_to_id, is_active=True).first() if assigned_to_id else None
    pm = PreventiveMaintenance.objects.create(
        equipment=e,
        title=title,
        frequency_months=freq,
        next_due_date=next_due,
        advance_notice_days=notice,
        assigned_to=assigned_to,
        notes=notes,
    )
    messages.success(request, f"Preventive maintenance schedule '{pm.title}' created.")
    return redirect("equipment_detail", pk=e.pk)


@not_requester
@require_POST
def pm_trigger_ticket(request, pk):
    pm = get_object_or_404(PreventiveMaintenance, pk=pk)
    if pm.last_ticket and pm.last_ticket.status in OPEN_STATUSES:
        messages.warning(request, f"Ticket {pm.last_ticket.number} is already open for this PM schedule.")
        return redirect("ticket_detail", number=pm.last_ticket.number)
    services.run_pm_checks()
    pm.refresh_from_db()
    if pm.last_ticket:
        messages.success(request, f"PM ticket {pm.last_ticket.number} generated.")
        return redirect("ticket_detail", number=pm.last_ticket.number)
    messages.info(request, "PM checks executed.")
    return redirect("equipment_detail", pk=pm.equipment_id)


# --------------------------------------------------------------------------
# Dashboard
# --------------------------------------------------------------------------
@not_requester
def dashboard(request):
    qs = services.visible_tickets(request.user)
    centre = request.GET.get("centre", "")
    department = request.GET.get("department", "")
    if centre.isdigit():
        qs = qs.filter(centre_id=int(centre))
    if department.isdigit():
        qs = qs.filter(department_id=int(department))
    days = int(request.GET["days"]) if request.GET.get("days") in ("7", "30", "90") else 30
    ctx = {"d": reports.dashboard(qs, days), "centres": Centre.objects.filter(active=True),
           "departments": Department.objects.filter(active=True), "centre": centre, "department": department}
    return render(request, "dashboard.html", ctx)


# --------------------------------------------------------------------------
# Alerts
# --------------------------------------------------------------------------
@login_required
def notifications(request):
    if request.method == "POST":
        Notification.objects.filter(user=request.user, read=False).update(read=True)
        return redirect("notifications")
    page = Paginator(Notification.objects.filter(user=request.user).select_related("ticket"), 30).get_page(
        request.GET.get("page"))
    return render(request, "notifications.html", {"page": page})


@login_required
def notification_open(request, pk):
    n = get_object_or_404(Notification, pk=pk, user=request.user)
    n.read = True
    n.save(update_fields=["read"])
    return redirect("ticket_detail", number=n.ticket.number) if n.ticket else redirect("notifications")


# --------------------------------------------------------------------------
# Settings and system health (administrators)
# --------------------------------------------------------------------------
class SettingsForm(forms.ModelForm):
    class Meta:
        model = SiteSettings
        fields = "__all__"
        widgets = {
            "brand_color": forms.TextInput(attrs={"type": "color"}),
            "brand_color_2": forms.TextInput(attrs={"type": "color"}),
            "smtp_password": forms.PasswordInput(render_value=False, attrs={"placeholder": "Saved. Type a new one to change it", "autocomplete": "new-password"}),
            "sms_headers": forms.Textarea(attrs={"rows": 2}), "sms_body": forms.Textarea(attrs={"rows": 2}),
            "whatsapp_headers": forms.Textarea(attrs={"rows": 2}),
            "whatsapp_body": forms.Textarea(attrs={"rows": 2}),
            "telegram_bot_token": forms.TextInput(attrs={"placeholder": "From @BotFather, e.g. 123456:ABC-DEF1234ghIkl-zyx57W2v1u123ew11", "autocomplete": "off", "spellcheck": "false"}),
        }

    def clean_telegram_bot_token(self):
        token = self.cleaned_data.get("telegram_bot_token") or ""
        return token.strip()

    def clean_telegram_default_chat_id(self):
        chat_id = self.cleaned_data.get("telegram_default_chat_id") or ""
        return chat_id.strip()

    def clean_smtp_password(self):
        # The saved password is never sent back to the browser; an empty box keeps it.
        return self.cleaned_data.get("smtp_password") or self.instance.smtp_password


def _health():
    now = timezone.now()
    beat = Heartbeat.objects.filter(name="worker").first()
    worker_ok = bool(beat and now - beat.at < timedelta(minutes=5))
    backup = {"ok": False, "text": "No backup has run yet.", "when": None}
    try:
        data = json.loads((dj_settings.STATUS_DIR / "backup.json").read_text())
        when = datetime.fromisoformat(data["finished"])
        if timezone.is_naive(when):
            when = timezone.make_aware(when)
        fresh = now - when < timedelta(hours=26)
        good = bool(data.get("ok"))
        if good and fresh:
            text = data.get("message") or "Backup completed."
        elif good:
            text = "The last backup is more than a day old. Check that the NAS is connected."
        else:
            text = data.get("message") or "The last backup failed."
        backup = {"ok": good and fresh, "when": when, "size": data.get("size", ""), "text": text}
    except (OSError, ValueError, KeyError):
        pass
    return {
        "worker_ok": worker_ok, "worker": beat, "backup": backup,
        "pending": OutboundMessage.objects.filter(status=OutboundMessage.PENDING).count(),
        "failed": OutboundMessage.objects.filter(status=OutboundMessage.FAILED,
                                                 created_at__gte=now - timedelta(days=7)).count(),
        "last_failed": OutboundMessage.objects.filter(status=OutboundMessage.FAILED).first(),
        "tickets": Ticket.objects.count(), "users": User.objects.filter(is_active=True).count(),
    }


@admin_only
def settings_view(request):
    s = SiteSettings.load()
    form = SettingsForm(request.POST or None, request.FILES or None, instance=s)
    if request.method == "POST":
        if "test" in request.POST:
            channel = request.POST["test"]
            if channel == "email":
                to = request.user.email
                missing_msg = "Add your own email address to your user record first."
            elif channel == "telegram":
                to = getattr(request.user, "telegram_chat_id", "") or s.telegram_default_chat_id
                missing_msg = "Add your Telegram Chat ID in My Profile or set a Default Telegram group/channel chat ID below first."
            else:
                to = request.user.mobile
                missing_msg = "Add your mobile number to your user record first."
            if not to:
                messages.error(request, missing_msg)
            else:
                msg = OutboundMessage(channel=channel, to=to, subject=f"[{s.org_name}] Test alert",
                                      body="This is a test alert from DiagTrack. If you can read it, alerts work.")
                try:
                    notify.send_one(msg, s)
                    messages.success(request, f"Test sent to {to}. Check that it arrived.")
                except Exception as exc:
                    messages.error(request, f"The test could not be sent: {exc}")
            return redirect("settings")
        if form.is_valid():
            form.save()
            messages.success(request, "Settings saved.")
            return redirect("settings")
        messages.error(request, "Some settings could not be saved. Check the marked fields.")
    return render(request, "settings.html", {"form": form, "health": _health()})


@admin_only
def message_templates(request):
    """The wording of email, SMS and WhatsApp alerts, event by event."""
    s = SiteSettings.load()
    event = request.POST.get("event") or request.GET.get("event") or "raised"
    if event not in wording.EVENT_KEYS:
        event = "raised"
    here = f"{request.path}?event={event}"
    if request.method == "POST":
        what = request.POST.get("do", "save")
        if what == "reset":
            MessageTemplate.objects.filter(event=event).delete()
            messages.success(request, f"{wording.EVENT_LABELS[event]}: back to the built-in wording.")
            return redirect(here)
        sends = dict(MessageTemplate.SEND)
        problems = []
        for ch, label in wording.CHANNELS:
            body = request.POST.get(f"{ch}-body", "").strip()
            send = request.POST.get(f"{ch}-send", "always")
            if send not in sends:
                send = "always"
            if not body and send != MessageTemplate.NEVER:
                problems.append(f"The {label} message is empty.")
                continue
            unknown = sorted({k for k in wording.PLACE.findall(body + request.POST.get(f"{ch}-subject", ""))
                              if k not in wording.KEYS})
            if unknown:
                problems.append(f"{label}: " + ", ".join("{" + k + "}" for k in unknown) +
                                " is not a placeholder DiagTrack knows. It would be sent exactly as typed.")
            MessageTemplate.objects.update_or_create(event=event, channel=ch, defaults={
                "send": send, "body": body or wording.default(event, ch)[2],
                "subject": request.POST.get(f"{ch}-subject", "").strip()[:200],
                "provider_template": request.POST.get(f"{ch}-provider", "").strip()[:120],
                "updated_by": request.user})
        if what == "test":
            ctx = wording.sample(s)
            ctx["recipient"] = request.user.display_name
            tpl = {ch: wording.get(event, ch) for ch, on in
                   (("email", s.email_enabled), ("sms", s.sms_enabled), ("whatsapp", s.whatsapp_enabled), ("telegram", s.telegram_enabled)) if on}
            tg_id = getattr(request.user, "telegram_chat_id", "") or s.telegram_default_chat_id
            made = notify.queue(tpl, ctx, email=request.user.email, mobile=request.user.mobile, telegram_id=tg_id)
            if made:
                messages.success(request, "Test queued: " + ", ".join(f"{m.get_channel_display()} to {m.to}" for m in made) +
                                 ". It is sent within a minute; the result shows under Masters and rules, Sent alerts.")
            else:
                messages.error(request, "Nothing to send. Switch the channel on in Settings and add your own email "
                                        "and mobile number to your profile.")
        else:
            messages.success(request, f"{wording.EVENT_LABELS[event]}: wording saved.")
        for p in problems:
            messages.warning(request, p)
        return redirect(here)
    saved = {(m.event, m.channel): m for m in MessageTemplate.objects.all()}
    groups = {}
    for key, label, group, about in wording.EVENTS:
        row = {"key": key, "label": label, "about": about,
               "custom": any((key, c) in saved for c, _ in wording.CHANNELS),
               "sends": [(c, saved[(key, c)].send if (key, c) in saved else wording.default(key, c)[0])
                         for c, _ in wording.CHANNELS]}
        groups.setdefault(group, []).append(row)
    current = []
    for ch, label in wording.CHANNELS:
        t = wording.get(event, ch)
        current.append({"ch": ch, "label": label, **t,
                        "on": {"email": s.email_enabled, "sms": s.sms_enabled, "whatsapp": s.whatsapp_enabled, "telegram": s.telegram_enabled}[ch],
                        "limit": wording.LIMITS.get(ch, 0)})
    return render(request, "message_templates.html", {
        "event": event, "event_label": wording.EVENT_LABELS[event],
        "event_about": next(e[3] for e in wording.EVENTS if e[0] == event),
        "groups": groups.items(), "current": current, "placeholders": wording.PLACEHOLDERS,
        "sample_json": json.dumps(wording.sample(s)), "send_choices": MessageTemplate.SEND,
        "custom": any((event, c) in saved for c, _ in wording.CHANNELS),
    })


class PasswordResetView(auth_views.PasswordResetView):
    """Forgot-password email, sent from the address saved in Settings."""
    def form_valid(self, form):
        s = SiteSettings.load()
        self.from_email = s.from_email or s.smtp_user or None
        return super().form_valid(form)


class PasswordChangeView(auth_views.PasswordChangeView):
    """Changing password terminates all other active sessions across devices."""
    def form_valid(self, form):
        resp = super().form_valid(form)
        from . import signin
        signin.invalidate_user_sessions(self.request.user, keep_session_key=self.request.session.session_key)
        return resp


class PasswordResetConfirmView(auth_views.PasswordResetConfirmView):
    """Setting a new password from the emailed link also lifts a sign-in pause: only the owner has the link."""
    def form_valid(self, form):
        from . import signin
        signin.unlock(username=form.user.username)
        signin.invalidate_user_sessions(form.user)
        return super().form_valid(form)


@login_required
@require_POST
def invalidate_sessions_view(request):
    from . import signin
    count = signin.invalidate_user_sessions(request.user, keep_session_key=request.session.session_key)
    messages.success(request, f"Terminated {count} other active session(s). Your current session remains active.")
    return redirect("profile")


def healthz(request):
    Heartbeat.objects.count()  # touches the database
    return JsonResponse({"ok": True})


def error_403(request, exception=None):
    return render(request, "error.html", {"code": 403, "title": "You do not have access to this page",
                                          "text": "Your role does not include this screen. Ask the administrator if you need it."}, status=403)


def error_404(request, exception=None):
    return render(request, "error.html", {"code": 404, "title": "That page or ticket was not found",
                                          "text": "It may belong to a centre or department you do not cover."}, status=404)


# --------------------------------------------------------------------------
# Centre matrix
# --------------------------------------------------------------------------
@not_requester
def centres(request):
    user = request.user
    qs = services.visible_tickets(user)
    if user.sees_everything or user.role == User.DEPT_HEAD or not user.centres.exists():
        cs = Centre.objects.filter(active=True)
    else:
        cs = user.centres.filter(active=True)
    if request.GET.get("department", "").isdigit():
        qs = qs.filter(department_id=int(request.GET["department"]))
    days = int(request.GET["days"]) if request.GET.get("days") in ("7", "30", "90") else 30
    return render(request, "centres.html", {"m": reports.centre_matrix(qs, list(cs), days),
                                            "departments": Department.objects.filter(active=True)})


# --------------------------------------------------------------------------
# QR labels for equipment
# --------------------------------------------------------------------------
def _qr_target(request, e):
    base = SiteSettings.load().site_url.rstrip("/") or request.build_absolute_uri("/").rstrip("/")
    return f"{base}/tickets/new/?equipment={e.pk}"


@not_requester
def equipment_qr(request, pk):
    """The QR code as a standalone SVG image (with its XML namespace, so browsers show it in <img>),
    or as a PNG file to download with ?format=png."""
    import io

    import segno
    e = get_object_or_404(Equipment, pk=pk)
    qr = segno.make(_qr_target(request, e), error="m")
    buf = io.BytesIO()
    if request.GET.get("format") == "png":
        qr.save(buf, kind="png", scale=12, border=3, dark="#0d1b2e")
        resp = HttpResponse(buf.getvalue(), content_type="image/png")
        resp["Content-Disposition"] = f'attachment; filename="QR-{e.centre.code}-{e.pk}.png"'
    else:
        qr.save(buf, kind="svg", scale=6, border=2, dark="#0d1b2e", xmldecl=False)
        resp = HttpResponse(buf.getvalue(), content_type="image/svg+xml")
    resp["Cache-Control"] = "private, max-age=300"
    return resp


@not_requester
def equipment_labels(request):
    items = Equipment.objects.filter(active=True).select_related("centre")
    if request.GET.get("centre", "").isdigit():
        items = items.filter(centre_id=int(request.GET["centre"]))
    if request.GET.get("id", "").isdigit():
        items = items.filter(pk=int(request.GET["id"]))
    return render(request, "equipment_labels.html", {"items": items})


# --------------------------------------------------------------------------
# Small data feeds for the live screen, search and @mentions
# --------------------------------------------------------------------------
@login_required
def api_live(request):
    """New alerts since the last one the screen has seen. Polled every 15 seconds."""
    after = int(request.GET.get("after") or 0)
    items = []
    for n in Notification.objects.filter(user=request.user, pk__gt=after).select_related("ticket").order_by("pk")[:10]:
        items.append({"id": n.pk, "kind": n.kind, "title": n.title, "body": n.body, "urgent": n.urgent,
                      "url": f"/alerts/{n.pk}/", "number": n.ticket.number if n.ticket else "",
                      "priority": n.ticket.priority if n.ticket else ""})
    last = Notification.objects.filter(user=request.user).order_by("-pk").values_list("pk", flat=True).first() or 0
    return JsonResponse({"unread": Notification.objects.filter(user=request.user, read=False).count(),
                         "items": items, "last": last})


@login_required
def api_search(request):
    q = request.GET.get("q", "").strip()
    out = []
    if len(q) >= 2:
        qs = services.visible_tickets(request.user).filter(
            Q(number__icontains=q) | Q(subject__icontains=q) | Q(equipment__name__icontains=q)).order_by("-created_at")[:8]
        out = [{"number": t.number, "subject": t.subject, "status": t.get_status_display(), "priority": t.priority,
                "meta": f"{t.centre.name}, {t.department.name}"} for t in qs]
    return JsonResponse({"tickets": out})


@login_required
def api_people(request):
    q = request.GET.get("q", "").strip()
    qs = User.objects.filter(is_active=True)
    if q:
        qs = qs.filter(Q(username__istartswith=q) | Q(first_name__istartswith=q) | Q(last_name__istartswith=q))
    return JsonResponse({"people": [{"username": u.username, "name": u.display_name, "initials": u.initials,
                                     "hue": u.hue, "role": u.role_label} for u in qs.order_by("first_name")[:8]]})


# --------------------------------------------------------------------------
# Branding and profile photos
# --------------------------------------------------------------------------
SAFE_FILE_HEADERS = {"Content-Security-Policy": "default-src 'none'; img-src 'self' data:; style-src 'unsafe-inline'",
                     "X-Content-Type-Options": "nosniff"}


def _safe(resp):
    """Files people upload are shown as files only: no scripts can run from them."""
    for k, v in SAFE_FILE_HEADERS.items():
        resp[k] = v
    return resp


def brand_logo(request):
    s = SiteSettings.load()
    if not s.logo:
        raise Http404
    try:
        return _safe(FileResponse(s.logo.open("rb")))
    except FileNotFoundError:
        raise Http404


def brand_logo_wide(request):
    s = SiteSettings.load()
    if not s.logo_wide:
        raise Http404
    try:
        return _safe(FileResponse(s.logo_wide.open("rb")))
    except FileNotFoundError:
        raise Http404


@login_required
def person_photo(request, pk):
    u = get_object_or_404(User, pk=pk)
    if not u.photo:
        raise Http404
    try:
        return _safe(FileResponse(u.photo.open("rb")))
    except FileNotFoundError:
        raise Http404


class ProfileForm(forms.ModelForm):
    class Meta:
        model = User
        fields = ["first_name", "last_name", "email", "mobile", "telegram_chat_id", "photo"]


@login_required
def profile(request):
    form = ProfileForm(request.POST or None, request.FILES or None, instance=request.user)
    if request.method == "POST":
        if request.POST.get("remove_photo"):
            request.user.photo = ""
            request.user.save(update_fields=["photo"])
            messages.success(request, "Photo removed.")
            return redirect("profile")
        if request.POST.get("test_telegram"):
            chat_id = (request.POST.get("telegram_chat_id") or request.user.telegram_chat_id or "").strip()
            if not chat_id:
                messages.error(request, "Enter your Telegram Chat ID first.")
            else:
                if form.is_valid():
                    form.save()
                else:
                    request.user.telegram_chat_id = chat_id
                    request.user.save(update_fields=["telegram_chat_id"])
                s = SiteSettings.load()
                if not s.telegram_bot_token:
                    messages.error(request, "Telegram bot is not configured yet. Ask an administrator to set the bot token in Settings.")
                else:
                    msg = OutboundMessage(
                        channel=OutboundMessage.TELEGRAM,
                        to=chat_id,
                        subject=f"[{s.org_name}] Test alert",
                        body=f"Hello {request.user.display_name}! This is a test alert from {s.org_name} DiagTrack. Your Telegram notifications are working."
                    )
                    try:
                        notify.send_one(msg, s)
                        messages.success(request, f"Test alert sent to Telegram chat ID {chat_id}. Check your Telegram app!")
                    except Exception as exc:
                        messages.error(request, f"The test could not be sent: {exc}")
            return redirect("profile")
        if form.is_valid():
            f = request.FILES.get("photo")
            if f and (f.size > 3 * 1024 * 1024 or f.name.lower().rsplit(".", 1)[-1] not in ("jpg", "jpeg", "png", "webp")):
                form.add_error("photo", "Choose a JPG, PNG or WEBP image smaller than 3 MB.")
            else:
                form.save()
                messages.success(request, "Profile saved.")
                return redirect("profile")
    return render(request, "profile.html", {"form": form})


# --------------------------------------------------------------------------
# Reports: on screen, Excel, and a print view for PDF
# --------------------------------------------------------------------------
from . import exports  # noqa: E402


def _report_context(request):
    r = exports.build_report(request.user, request.GET)
    return {"r": r, "groups": exports.GROUPS, "periods": exports.PERIODS,
            "centres": Centre.objects.filter(active=True), "departments": Department.objects.filter(active=True),
            "priorities": Priority.choices, "query": request.GET.urlencode()}


@not_requester
def reports_view(request):
    return render(request, "reports.html", _report_context(request))


@not_requester
def report_excel(request):
    ctx = _report_context(request)
    s = SiteSettings.load()
    r = ctx["r"]
    data = exports.to_excel(r, s.org_name, request.user.display_name, s.brand_color)
    name = f"diagtrack-{r['group']}-{r['start']:%Y%m%d}-{r['end']:%Y%m%d}.xlsx"
    resp = HttpResponse(data, content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    resp["Content-Disposition"] = f'attachment; filename="{name}"'
    return resp


@not_requester
def report_print(request):
    return render(request, "report_print.html", _report_context(request))


@login_required
def api_duplicates(request):
    centre_id = request.GET.get("centre", "").strip()
    equipment_id = request.GET.get("equipment", "").strip()
    q = request.GET.get("q", "").strip()

    q_filter = Q()
    has_filter = False

    if equipment_id and equipment_id.isdigit():
        q_filter |= Q(equipment_id=int(equipment_id))
        has_filter = True

    if centre_id and centre_id.isdigit() and len(q) >= 3:
        try:
            cid = int(centre_id)
            words = [w for w in re.split(r"\s+", q) if len(w) >= 3]
            if words:
                word_q = Q()
                for w in words[:4]:
                    word_q |= Q(subject__icontains=w)
                q_filter |= (Q(centre_id=cid) & word_q)
                has_filter = True
        except (ValueError, TypeError):
            pass

    if not has_filter:
        return JsonResponse({"duplicates": []})

    qs = services.visible_tickets(request.user).filter(status__in=OPEN_STATUSES).filter(q_filter)
    out = []
    seen = set()
    for t in qs.select_related("centre", "department", "assignee", "equipment").order_by("-created_at")[:5]:
        if t.pk in seen:
            continue
        seen.add(t.pk)
        out.append({
            "number": t.number,
            "subject": t.subject,
            "status": t.get_status_display(),
            "priority": t.priority,
            "centre": t.centre.name,
            "department": t.department.name,
            "assignee": t.assignee.display_name if t.assignee else "Unassigned",
            "equipment": t.equipment.name if t.equipment else "",
            "url": f"/tickets/{t.number}/",
        })
    return JsonResponse({"duplicates": out})


@login_required
@require_POST
def ticket_bulk(request):
    numbers_raw = request.POST.get("ticket_numbers", "")
    action = request.POST.get("bulk_action", "").strip()
    numbers = [n.strip() for n in numbers_raw.split(",") if n.strip()]
    if not numbers:
        numbers = [n.strip() for n in request.POST.getlist("bulk_ticket") if n.strip()]

    if not numbers or not action:
        if request.headers.get("x-requested-with") == "XMLHttpRequest":
            return JsonResponse({"ok": False, "error": "No tickets or action selected."}, status=400)
        messages.error(request, "Please select at least one ticket and an action.")
        return redirect(request.META.get("HTTP_REFERER") or "ticket_list")

    tickets = list(services.visible_tickets(request.user).filter(number__in=numbers))
    count = 0
    errors = 0

    for t in tickets:
        allowed = services.allowed_actions(request.user, t)
        try:
            if action == "assign_me":
                if "assign_me" in allowed or "assign" in allowed:
                    services.assign(t, request.user, request.user, check=False)
                    count += 1
                else:
                    errors += 1
            elif action.startswith("priority_"):
                prio = action.split("_", 1)[1].upper()
                if prio in Priority.values:
                    if "set_priority" in allowed:
                        services.change_priority(t, request.user, prio, "Bulk priority update")
                        count += 1
                    elif "raise_priority" in allowed and prio < t.priority:
                        services.change_priority(t, request.user, prio, "Bulk priority update")
                        count += 1
                    else:
                        errors += 1
            elif action == "start":
                if "start" in allowed:
                    services.start_work(t, request.user)
                    count += 1
                else:
                    errors += 1
            elif action == "close":
                if "close" in allowed:
                    services.close(t, request.user)
                    count += 1
                else:
                    errors += 1
            elif action == "cancel":
                if "cancel" in allowed:
                    services.cancel(t, request.user, "Bulk cancellation")
                    count += 1
                else:
                    errors += 1
        except Exception:
            errors += 1

    action_label = action.replace("_", " ")
    msg = f"Applied \x27{action_label}\x27 to {count} ticket(s)."
    if errors:
        msg += f" ({errors} ticket(s) skipped due to permissions or status restrictions)."

    if request.headers.get("x-requested-with") == "XMLHttpRequest":
        return JsonResponse({"ok": True, "count": count, "message": msg})
    messages.success(request, msg)
    return redirect(request.META.get("HTTP_REFERER") or "ticket_list")


def service_worker(request):
    sw_path = dj_settings.BASE_DIR / "static" / "sw.js"
    content = sw_path.read_text(encoding="utf-8") if sw_path.exists() else ""
    response = HttpResponse(content, content_type="application/javascript")
    response["Service-Worker-Allowed"] = "/"
    response["Cache-Control"] = "no-cache"
    return response


@login_required
def api_live_stream(request):
    """Server-Sent Events (SSE) stream for real-time alerts without polling overhead."""
    def event_stream():
        last = int(request.GET.get("after") or 0)
        yield ": connected\n\n"
        max_loops = 1 if request.GET.get("test") else 25
        for _ in range(max_loops):
            new_alerts = list(Notification.objects.filter(user=request.user, pk__gt=last).select_related("ticket").order_by("pk")[:10])
            if new_alerts:
                items = [{"id": n.pk, "kind": n.kind, "title": n.title, "body": n.body, "urgent": n.urgent,
                          "url": f"/alerts/{n.pk}/", "number": n.ticket.number if n.ticket else "",
                          "priority": n.ticket.priority if n.ticket else ""} for n in new_alerts]
                unread = Notification.objects.filter(user=request.user, read=False).count()
                last = new_alerts[-1].pk
                payload = json.dumps({"unread": unread, "items": items, "last": last})
                yield f"event: alert\ndata: {payload}\n\n"
            else:
                yield ": keep-alive\n\n"
            if max_loops > 1:
                time.sleep(2)

    resp = StreamingHttpResponse(event_stream(), content_type="text/event-stream")
    resp["Cache-Control"] = "no-cache"
    resp["X-Accel-Buffering"] = "no"
    return resp


def metrics_view(request):
    """Prometheus-compatible metrics endpoint for system observability."""
    now = timezone.now()

    lines = []
    def add_metric(name, help_text, m_type, samples):
        lines.append(f"# HELP {name} {help_text}")
        lines.append(f"# TYPE {name} {m_type}")
        for labels, val in samples:
            if labels:
                lbls = ",".join(f"{k}=\"{v}\"" for k, v in labels.items())
                lines.append(f"{name}{{{lbls}}} {val}")
            else:
                lines.append(f"{name} {val}")
        lines.append("")

    # Tickets by status
    status_samples = [({"status": s}, Ticket.objects.filter(status=s, is_archived=False).count()) for s, _ in Status.choices]
    add_metric("diagtrack_tickets_by_status", "Current tickets by status", "gauge", status_samples)

    # Open tickets by priority
    prio_samples = [({"priority": p}, Ticket.objects.filter(priority=p, status__in=OPEN_STATUSES, is_archived=False).count()) for p, _ in Priority.choices]
    add_metric("diagtrack_tickets_open_by_priority", "Current open tickets by priority", "gauge", prio_samples)

    # SLA breaches
    resp_b = Ticket.objects.filter(response_breached=True, is_archived=False).count()
    resol_b = Ticket.objects.filter(resolution_breached=True, is_archived=False).count()
    add_metric("diagtrack_sla_breaches_total", "Tickets that breached SLA deadlines", "gauge", [
        ({"type": "response"}, resp_b),
        ({"type": "resolution"}, resol_b),
    ])

    # Active equipment downtime
    down_count = DowntimeLog.objects.filter(end__isnull=True).count()
    add_metric("diagtrack_equipment_down_active", "Number of equipment units currently in downtime", "gauge", [({}, down_count)])

    # Outbound queue
    pending_m = OutboundMessage.objects.filter(status=OutboundMessage.PENDING).count()
    failed_m = OutboundMessage.objects.filter(status=OutboundMessage.FAILED).count()
    add_metric("diagtrack_outbound_messages", "Outbound notification messages queue", "gauge", [
        ({"status": "pending"}, pending_m),
        ({"status": "failed"}, failed_m),
    ])

    # Worker heartbeat age
    hb = Heartbeat.objects.filter(name="worker").first()
    hb_age = (now - hb.at).total_seconds() if hb else -1
    add_metric("diagtrack_worker_heartbeat_age_seconds", "Seconds since worker last executed", "gauge", [({}, round(hb_age, 1))])

    resp = HttpResponse("\n".join(lines), content_type="text/plain; version=0.0.4; charset=utf-8")
    resp["Cache-Control"] = "no-cache"
    return resp



# --------------------------------------------------------------------------
# Masters and Rules Hub (Integrated Native UI)
# --------------------------------------------------------------------------
@admin_only
def masters_hub(request):
    tab = request.GET.get("tab") or "centres"
    valid_tabs = (
        "centres", "departments", "categories", "sla", "escalation",
        "users", "locks", "vendors", "equipment", "pm",
        "alerts", "history", "vendor_calls"
    )
    if tab not in valid_tabs:
        tab = "centres"
    q = request.GET.get("q", "").strip()
    sort = request.GET.get("sort", "").strip()
    status_filter = request.GET.get("status", "").strip()
    centre_filter = request.GET.get("centre", "").strip()
    dept_filter = request.GET.get("department", "").strip()
    role_filter = request.GET.get("role", "").strip()
    priority_filter = request.GET.get("priority", "").strip()
    vendor_filter = request.GET.get("vendor", "").strip()
    freq_filter = request.GET.get("frequency", "").strip()
    mode_filter = request.GET.get("mode", "").strip()
    basis_filter = request.GET.get("basis", "").strip()

    ctx = {
        "tab": tab,
        "q": q,
        "sort": sort,
        "status_filter": status_filter,
        "centre_filter": centre_filter,
        "dept_filter": dept_filter,
        "role_filter": role_filter,
        "priority_filter": priority_filter,
        "vendor_filter": vendor_filter,
        "freq_filter": freq_filter,
        "mode_filter": mode_filter,
        "basis_filter": basis_filter,
        "priorities": Priority.choices,
        "centres_all": Centre.objects.filter(active=True).order_by("name"),
        "departments_all": Department.objects.filter(active=True).order_by("name"),
        "users_all": User.objects.filter(is_active=True).order_by("first_name", "username"),
        "vendors_all": Vendor.objects.all().order_by("name"),
        "equipment_all": Equipment.objects.filter(active=True).select_related("centre").order_by("centre__name", "name"),
    }

    if tab == "centres":
        qs = Centre.objects.all().annotate(ticket_count=Count("tickets", distinct=True), equipment_count=Count("equipment", distinct=True))
        if q:
            qs = qs.filter(Q(name__icontains=q) | Q(code__icontains=q) | Q(city__icontains=q))
        if status_filter == "active":
            qs = qs.filter(active=True)
        elif status_filter == "inactive":
            qs = qs.filter(active=False)
        if sort == "name_desc":
            qs = qs.order_by("-name")
        elif sort == "code":
            qs = qs.order_by("code")
        elif sort == "tickets":
            qs = qs.order_by("-ticket_count", "name")
        elif sort == "equipment":
            qs = qs.order_by("-equipment_count", "name")
        elif sort == "city":
            qs = qs.order_by("city", "name")
        else:
            qs = qs.order_by("name")
        ctx["centres"] = qs

    elif tab == "departments":
        qs = Department.objects.all().select_related("head").annotate(agent_count=Count("members"))
        if q:
            qs = qs.filter(name__icontains=q)
        if status_filter == "active":
            qs = qs.filter(active=True)
        elif status_filter == "inactive":
            qs = qs.filter(active=False)
        if mode_filter:
            qs = qs.filter(assignment_mode=mode_filter)
        if request.GET.get("handles_equipment") == "1":
            qs = qs.filter(handles_equipment=True)
        if sort == "name_desc":
            qs = qs.order_by("-name")
        elif sort == "agents":
            qs = qs.order_by("-agent_count", "name")
        elif sort == "mode":
            qs = qs.order_by("assignment_mode", "name")
        else:
            qs = qs.order_by("name")
        ctx["departments"] = qs
        ctx["assignment_modes"] = Department.MODES

    elif tab == "categories":
        qs = Category.objects.all().select_related("department").prefetch_related("fields")
        if q:
            qs = qs.filter(Q(name__icontains=q) | Q(department__name__icontains=q))
        if dept_filter.isdigit():
            qs = qs.filter(department_id=int(dept_filter))
        if priority_filter:
            qs = qs.filter(default_priority=priority_filter)
        if status_filter == "active":
            qs = qs.filter(active=True)
        elif status_filter == "inactive":
            qs = qs.filter(active=False)
        if request.GET.get("asks_eq") == "1":
            qs = qs.filter(asks_equipment=True)
        if sort == "name_desc":
            qs = qs.order_by("-name")
        elif sort == "priority":
            qs = qs.order_by("default_priority", "name")
        elif sort == "department":
            qs = qs.order_by("department__name", "name")
        else:
            qs = qs.order_by("department__name", "name")
        ctx["categories"] = qs

    elif tab == "sla":
        qs = SlaPolicy.objects.all().select_related("department")
        if q:
            qs = qs.filter(Q(department__name__icontains=q) | Q(priority__icontains=q))
        if dept_filter.isdigit():
            qs = qs.filter(department_id=int(dept_filter))
        elif dept_filter == "default":
            qs = qs.filter(department__isnull=True)
        if priority_filter:
            qs = qs.filter(priority=priority_filter)
        if sort == "response":
            qs = qs.order_by("response_minutes")
        elif sort == "resolution":
            qs = qs.order_by("resolution_minutes")
        elif sort == "priority":
            qs = qs.order_by("priority", "department__name")
        else:
            qs = qs.order_by("department__name", "priority")
        ctx["sla_policies"] = qs

    elif tab == "escalation":
        qs = EscalationRule.objects.all()
        if q:
            qs = qs.filter(name__icontains=q)
        if basis_filter:
            qs = qs.filter(basis=basis_filter)
        if status_filter == "active":
            qs = qs.filter(active=True)
        elif status_filter == "disabled":
            qs = qs.filter(active=False)
        if sort == "name":
            qs = qs.order_by("name")
        elif sort == "percent_desc":
            qs = qs.order_by("-percent")
        elif sort == "basis":
            qs = qs.order_by("basis", "percent")
        else:
            qs = qs.order_by("basis", "percent")
        ctx["escalation_rules"] = qs

    elif tab == "users":
        qs = User.objects.all().prefetch_related("centres", "departments")
        if q:
            qs = qs.filter(Q(username__icontains=q) | Q(first_name__icontains=q) | Q(last_name__icontains=q) | Q(email__icontains=q) | Q(mobile__icontains=q))
        if role_filter:
            qs = qs.filter(role=role_filter)
        if status_filter == "active":
            qs = qs.filter(is_active=True)
        elif status_filter == "inactive":
            qs = qs.filter(is_active=False)
        if centre_filter.isdigit():
            qs = qs.filter(centres__id=int(centre_filter)).distinct()
        if dept_filter.isdigit():
            qs = qs.filter(departments__id=int(dept_filter)).distinct()
        if sort == "name":
            qs = qs.order_by("first_name", "last_name")
        elif sort == "username":
            qs = qs.order_by("username")
        elif sort == "role":
            qs = qs.order_by("role", "first_name")
        else:
            qs = qs.order_by("role", "first_name")
        locks = {lock.key for lock in SignInLock.objects.filter(locked_until__gt=timezone.now())}
        staff_list = list(qs)
        for u in staff_list:
            u.is_locked = f"user:{u.username}" in locks
        ctx["staff"] = staff_list
        ctx["roles"] = User.ROLES

    elif tab == "locks":
        qs = SignInLock.objects.all()
        if q:
            qs = qs.filter(Q(key__icontains=q) | Q(last_address__icontains=q))
        now = timezone.now()
        if status_filter == "paused":
            qs = qs.filter(locked_until__gt=now)
        elif status_filter == "counting":
            qs = qs.filter(Q(locked_until__isnull=True) | Q(locked_until__lte=now))
        if sort == "failures":
            qs = qs.order_by("-failures")
        elif sort == "recent":
            qs = qs.order_by("-first_failure")
        elif sort == "first":
            qs = qs.order_by("first_failure")
        else:
            qs = qs.order_by("-locked_until", "-first_failure")
        locks_list = list(qs)
        for lock in locks_list:
            lock.is_active_lock = bool(lock.locked_until and lock.locked_until > now)
        ctx["locks"] = locks_list

    elif tab == "vendors":
        qs = Vendor.objects.all().annotate(equipment_count=Count("equipment"))
        if q:
            qs = qs.filter(Q(name__icontains=q) | Q(contact_person__icontains=q) | Q(phone__icontains=q))
        if sort == "name_desc":
            qs = qs.order_by("-name")
        elif sort == "machines":
            qs = qs.order_by("-equipment_count", "name")
        else:
            qs = qs.order_by("name")
        ctx["vendors"] = qs

    elif tab == "equipment":
        qs = Equipment.objects.all().select_related("centre", "vendor")
        if q:
            qs = qs.filter(Q(name__icontains=q) | Q(make__icontains=q) | Q(model__icontains=q) | Q(serial_no__icontains=q) | Q(centre__name__icontains=q))
        if centre_filter.isdigit():
            qs = qs.filter(centre_id=int(centre_filter))
        if vendor_filter.isdigit():
            qs = qs.filter(vendor_id=int(vendor_filter))
        if status_filter == "active":
            qs = qs.filter(active=True)
        elif status_filter == "retired":
            qs = qs.filter(active=False)
        today = timezone.localdate()
        amc_filter = request.GET.get("amc", "").strip()
        if amc_filter == "expired":
            qs = qs.filter(amc_expiry__lt=today)
        elif amc_filter == "soon":
            qs = qs.filter(amc_expiry__gte=today, amc_expiry__lte=today + timedelta(days=30))
        elif amc_filter == "active":
            qs = qs.filter(amc_expiry__gt=today + timedelta(days=30))
        ctx["amc_filter"] = amc_filter
        if sort == "name_desc":
            qs = qs.order_by("-name")
        elif sort == "centre":
            qs = qs.order_by("centre__name", "name")
        elif sort == "amc":
            qs = qs.order_by("amc_expiry", "name")
        elif sort == "guarantee":
            qs = qs.order_by("-uptime_guarantee_percent", "name")
        elif sort == "penalty":
            qs = qs.order_by("-penalty_rate_per_day", "name")
        else:
            qs = qs.order_by("centre__name", "name")
        eq_list = list(qs)
        for e in eq_list:
            e.amc_left = (e.amc_expiry - today).days if e.amc_expiry else None
        ctx["equipment"] = eq_list

    elif tab == "pm":
        qs = PreventiveMaintenance.objects.all().select_related("equipment__centre", "assigned_to", "last_ticket")
        if q:
            qs = qs.filter(Q(title__icontains=q) | Q(equipment__name__icontains=q) | Q(equipment__centre__name__icontains=q))
        if centre_filter.isdigit():
            qs = qs.filter(equipment__centre_id=int(centre_filter))
        if freq_filter.isdigit():
            qs = qs.filter(frequency_months=int(freq_filter))
        if status_filter == "active":
            qs = qs.filter(active=True)
        elif status_filter == "disabled":
            qs = qs.filter(active=False)
        if sort == "due_desc":
            qs = qs.order_by("-next_due_date", "equipment__name")
        elif sort == "machine":
            qs = qs.order_by("equipment__name", "next_due_date")
        elif sort == "title":
            qs = qs.order_by("title", "next_due_date")
        else:
            qs = qs.order_by("next_due_date", "equipment__name")
        today = timezone.localdate()
        pm_items = list(qs)
        for pm in pm_items:
            days_left = (pm.next_due_date - today).days
            pm.days_left = days_left
            pm.days_overdue = abs(days_left) if days_left < 0 else 0
            pm.due_status = "overdue" if days_left < 0 else "due_soon" if days_left <= pm.advance_notice_days else "scheduled"
        due_state = request.GET.get("due_state", "").strip()
        ctx["due_state"] = due_state
        if due_state == "overdue":
            pm_items = [p for p in pm_items if p.due_status == "overdue"]
        elif due_state == "due_soon":
            pm_items = [p for p in pm_items if p.due_status == "due_soon"]
        elif due_state == "scheduled":
            pm_items = [p for p in pm_items if p.due_status == "scheduled"]
        ctx["pm_list"] = pm_items
        ctx["frequency_choices"] = PreventiveMaintenance.FREQUENCY_CHOICES

    elif tab == "alerts":
        qs = OutboundMessage.objects.all()
        channel = request.GET.get("channel", "").strip()
        if channel:
            qs = qs.filter(channel=channel)
        if status_filter:
            qs = qs.filter(status=status_filter)
        if q:
            qs = qs.filter(Q(to__icontains=q) | Q(subject__icontains=q) | Q(body__icontains=q))
        if sort == "old":
            qs = qs.order_by("created_at")
        elif sort == "attempts":
            qs = qs.order_by("-attempts", "-created_at")
        else:
            qs = qs.order_by("-created_at")
        page = Paginator(qs, 40).get_page(request.GET.get("page"))
        ctx["page"] = page
        ctx["channel"] = channel
        ctx["channels"] = OutboundMessage.CHANNELS

    elif tab == "history":
        qs = TicketHistory.objects.all().select_related("ticket", "actor")
        if q:
            qs = qs.filter(Q(ticket__number__icontains=q) | Q(text__icontains=q) | Q(old__icontains=q) | Q(new__icontains=q) | Q(actor__username__icontains=q))
        if sort == "old":
            qs = qs.order_by("created_at")
        else:
            qs = qs.order_by("-created_at")
        page = Paginator(qs, 50).get_page(request.GET.get("page"))
        ctx["page"] = page

    elif tab == "vendor_calls":
        qs = VendorCall.objects.all().select_related("ticket", "vendor", "logged_by")
        if q:
            qs = qs.filter(Q(ticket__number__icontains=q) | Q(vendor__name__icontains=q) | Q(reference__icontains=q) | Q(engineer__icontains=q))
        if vendor_filter.isdigit():
            qs = qs.filter(vendor_id=int(vendor_filter))
        if status_filter == "waiting":
            qs = qs.filter(attended_at__isnull=True)
        elif status_filter == "attended":
            qs = qs.filter(attended_at__isnull=False)
        if sort == "old":
            qs = qs.order_by("called_at")
        else:
            qs = qs.order_by("-called_at")
        page = Paginator(qs, 40).get_page(request.GET.get("page"))
        ctx["page"] = page

    return render(request, "masters_hub.html", ctx)


@admin_only
@require_POST
def masters_action(request, section):
    p = request.POST
    do = p.get("do", "")
    back = f"{reverse('masters_hub')}?tab={section}"

    if section == "centres":
        if do == "add":
            name = p.get("name", "").strip()
            code = p.get("code", "").strip().upper()
            city = p.get("city", "").strip()
            if not name or not code:
                messages.error(request, "Centre name and short code are required.")
                return redirect(back)
            if not re.match(r"^[A-Z0-9]{2,5}$", code):
                messages.error(request, "Short code must be 2 to 5 capital letters or digits.")
                return redirect(back)
            if Centre.objects.filter(code=code).exists():
                messages.error(request, f"Centre with code '{code}' already exists.")
                return redirect(back)
            centre = Centre.objects.create(name=name, code=code, city=city)
            messages.success(request, f"Centre '{centre.name}' ({centre.code}) created.")
        elif do == "edit":
            centre = get_object_or_404(Centre, pk=p.get("id") or 0)
            centre.name = p.get("name", "").strip() or centre.name
            centre.city = p.get("city", "").strip()
            centre.active = p.get("active") == "on"
            if not centre.tickets.exists() and p.get("code"):
                new_code = p.get("code", "").strip().upper()
                if re.match(r"^[A-Z0-9]{2,5}$", new_code):
                    centre.code = new_code
            centre.save()
            messages.success(request, f"Centre '{centre.name}' updated.")

    elif section == "departments":
        if do == "add":
            name = p.get("name", "").strip()
            head_id = p.get("head") or None
            mode = p.get("assignment_mode", Department.ROUND_ROBIN)
            handles_eq = p.get("handles_equipment") == "on"
            if not name:
                messages.error(request, "Department name is required.")
                return redirect(back)
            if Department.objects.filter(name__iexact=name).exists():
                messages.error(request, f"Department '{name}' already exists.")
                return redirect(back)
            if handles_eq:
                Department.objects.filter(handles_equipment=True).update(handles_equipment=False)
            Department.objects.create(name=name, head_id=head_id, assignment_mode=mode, handles_equipment=handles_eq)
            messages.success(request, f"Department '{name}' created.")
        elif do == "edit":
            dept = get_object_or_404(Department, pk=p.get("id") or 0)
            name = p.get("name", "").strip()
            head_id = p.get("head") or None
            mode = p.get("assignment_mode", Department.ROUND_ROBIN)
            handles_eq = p.get("handles_equipment") == "on"
            active = p.get("active") == "on"
            if not name:
                messages.error(request, "Department name is required.")
                return redirect(back)
            if handles_eq and not dept.handles_equipment:
                Department.objects.filter(handles_equipment=True).update(handles_equipment=False)
            dept.name, dept.head_id, dept.assignment_mode, dept.handles_equipment, dept.active = name, head_id, mode, handles_eq, active
            dept.save()
            messages.success(request, f"Department '{dept.name}' updated.")

    elif section == "categories":
        if do == "add":
            name = p.get("name", "").strip()
            dept_id = p.get("department") or None
            prio = p.get("default_priority", Priority.P3)
            asks_eq = p.get("asks_equipment") == "on"
            asks_desc = p.get("asks_description") == "on"
            if not name or not dept_id:
                messages.error(request, "Name and Department are required.")
                return redirect(back)
            dept = get_object_or_404(Department, pk=dept_id)
            if Category.objects.filter(department=dept, name__iexact=name).exists():
                messages.error(request, f"Category '{name}' already exists in {dept.name}.")
                return redirect(back)
            cat = Category.objects.create(name=name, department=dept, default_priority=prio, asks_equipment=asks_eq,
                                          asks_description=asks_desc)
            messages.success(request, f"Category '{cat.name}' created.")
        elif do == "add_field":
            cat = get_object_or_404(Category, pk=p.get("category_id") or 0)
            label = p.get("label", "").strip()
            kind = p.get("kind", CategoryField.TEXT)
            choices = p.get("choices", "").strip()
            req = p.get("required") == "on"
            if not label:
                messages.error(request, "Question label is required.")
                return redirect(back)
            CategoryField.objects.create(category=cat, label=label, kind=kind, choices=choices, required=req, order=cat.fields.count())
            messages.success(request, f"Question '{label}' added to {cat.name}.")
        elif do == "edit":
            cat = get_object_or_404(Category, pk=p.get("id") or 0)
            cat.name = p.get("name", "").strip() or cat.name
            cat.default_priority = p.get("default_priority", cat.default_priority)
            cat.asks_equipment = p.get("asks_equipment") == "on"
            cat.asks_description = p.get("asks_description") == "on"
            cat.active = p.get("active") == "on"
            cat.save()
            messages.success(request, f"Category '{cat.name}' updated.")

    elif section == "sla":
        if do == "save":
            dept_id = p.get("department") or None
            prio = p.get("priority")
            resp_m = int(p.get("response_minutes") or 60)
            resol_m = int(p.get("resolution_minutes") or 1440)
            if not prio:
                messages.error(request, "Priority is required.")
                return redirect(back)
            dept = Department.objects.filter(pk=dept_id).first() if dept_id else None
            policy, created = SlaPolicy.objects.update_or_create(
                department=dept, priority=prio,
                defaults={"response_minutes": resp_m, "resolution_minutes": resol_m}
            )
            messages.success(request, f"SLA policy for {dept.name if dept else 'All departments'} {prio} saved.")

    elif section == "escalation":
        if do == "add":
            name = p.get("name", "").strip()
            basis = p.get("basis", EscalationRule.RESOLUTION)
            pct = int(p.get("percent") or 100)
            prios = p.get("priorities", "").strip()
            if not name:
                messages.error(request, "Rule name is required.")
                return redirect(back)
            EscalationRule.objects.create(
                name=name, basis=basis, percent=pct, priorities=prios,
                notify_assignee=p.get("notify_assignee") == "on",
                notify_dept_head=p.get("notify_dept_head") == "on",
                notify_centre_manager=p.get("notify_centre_manager") == "on",
                notify_area_manager=p.get("notify_area_manager") == "on",
            )
            messages.success(request, f"Escalation rule '{name}' created.")
        elif do == "edit":
            rule = get_object_or_404(EscalationRule, pk=p.get("id") or 0)
            name = p.get("name", "").strip()
            basis = p.get("basis", EscalationRule.RESOLUTION)
            pct = int(p.get("percent") or 100)
            prios = p.get("priorities", "").strip()
            if not name:
                messages.error(request, "Rule name is required.")
                return redirect(back)
            rule.name = name
            rule.basis = basis
            rule.percent = pct
            rule.priorities = prios
            rule.notify_assignee = p.get("notify_assignee") == "on"
            rule.notify_dept_head = p.get("notify_dept_head") == "on"
            rule.notify_centre_manager = p.get("notify_centre_manager") == "on"
            rule.notify_area_manager = p.get("notify_area_manager") == "on"
            rule.active = p.get("active") == "on"
            rule.save()
            messages.success(request, f"Escalation rule '{rule.name}' updated.")
        elif do == "toggle":
            rule = get_object_or_404(EscalationRule, pk=p.get("id") or 0)
            rule.active = not rule.active
            rule.save()
            messages.success(request, f"Rule '{rule.name}' {'activated' if rule.active else 'deactivated'}.")

    elif section == "users":
        if do == "unlock":
            username = p.get("username", "").strip()
            from . import signin
            signin.unlock(username=username)
            messages.success(request, f"Sign-in lock lifted for {username}.")
        elif do == "toggle_active":
            target_user = get_object_or_404(User, pk=p.get("id") or 0)
            if target_user.pk == request.user.pk:
                messages.error(request, "You cannot deactivate your own account.")
                return redirect(back)
            target_user.is_active = not target_user.is_active
            target_user.save()
            messages.success(request, f"User {target_user.username} {'activated' if target_user.is_active else 'deactivated'}.")
        elif do == "add":
            username = p.get("username", "").strip().lower()
            password = p.get("password", "").strip()
            first_name = p.get("first_name", "").strip()
            last_name = p.get("last_name", "").strip()
            role = p.get("role", User.REQUESTER)
            email = p.get("email", "").strip()
            mobile = p.get("mobile", "").strip()
            telegram_chat_id = p.get("telegram_chat_id", "").strip()
            if not username or not password:
                messages.error(request, "Username and password are required.")
                return redirect(back)
            if User.objects.filter(username=username).exists():
                messages.error(request, f"Username '{username}' is already taken.")
                return redirect(back)
            new_u = User.objects.create_user(
                username=username, password=password, first_name=first_name, last_name=last_name,
                email=email, mobile=mobile, telegram_chat_id=telegram_chat_id, role=role
            )
            if role == User.ADMIN:
                new_u.is_staff = new_u.is_superuser = True
                new_u.save()
            centres = p.getlist("centres")
            if centres:
                new_u.centres.set(centres)
            departments = p.getlist("departments")
            if departments:
                new_u.departments.set(departments)
            messages.success(request, f"User {new_u.display_name} (@{new_u.username}) created.")
        elif do == "edit":
            target_user = get_object_or_404(User, pk=p.get("id") or 0)
            first_name = p.get("first_name", "").strip()
            last_name = p.get("last_name", "").strip()
            role = p.get("role", target_user.role)
            email = p.get("email", "").strip()
            mobile = p.get("mobile", "").strip()
            telegram_chat_id = p.get("telegram_chat_id", "").strip()
            new_pw = p.get("new_password", "").strip()
            active = p.get("active") == "on"

            target_user.first_name = first_name
            target_user.last_name = last_name
            target_user.role = role
            target_user.email = email
            target_user.mobile = mobile
            target_user.telegram_chat_id = telegram_chat_id
            if target_user.pk != request.user.pk:
                target_user.is_active = active
            if role == User.ADMIN:
                target_user.is_staff = target_user.is_superuser = True
            elif target_user.is_superuser and role != User.ADMIN:
                target_user.is_staff = target_user.is_superuser = False
            if new_pw:
                target_user.set_password(new_pw)
                from . import signin
                signin.invalidate_user_sessions(target_user)
            target_user.save()
            centres = p.getlist("centres")
            target_user.centres.set(centres)
            departments = p.getlist("departments")
            target_user.departments.set(departments)
            messages.success(request, f"User {target_user.display_name} (@{target_user.username}) updated.")

    elif section == "locks":
        if do == "lift":
            key = p.get("key", "").strip()
            if key:
                SignInLock.objects.filter(key=key).delete()
                messages.success(request, f"Sign-in lock for {key} lifted.")

    elif section == "vendors":
        if do == "add":
            name = p.get("name", "").strip()
            contact = p.get("contact_person", "").strip()
            phone = p.get("phone", "").strip()
            email = p.get("email", "").strip()
            notes = p.get("notes", "").strip()
            if not name:
                messages.error(request, "Vendor name is required.")
                return redirect(back)
            Vendor.objects.create(name=name, contact_person=contact, phone=phone, email=email, notes=notes)
            messages.success(request, f"Vendor '{name}' added.")
        elif do == "edit":
            vendor = get_object_or_404(Vendor, pk=p.get("id") or 0)
            name = p.get("name", "").strip()
            contact = p.get("contact_person", "").strip()
            phone = p.get("phone", "").strip()
            email = p.get("email", "").strip()
            notes = p.get("notes", "").strip()
            if not name:
                messages.error(request, "Vendor name is required.")
                return redirect(back)
            vendor.name, vendor.contact_person, vendor.phone, vendor.email, vendor.notes = name, contact, phone, email, notes
            vendor.save()
            messages.success(request, f"Vendor '{vendor.name}' updated.")

    elif section == "equipment":
        if do == "add":
            centre_id = p.get("centre") or None
            name = p.get("name", "").strip()
            make = p.get("make", "").strip()
            model = p.get("model", "").strip()
            serial_no = p.get("serial_no", "").strip()
            vendor_id = p.get("vendor") or None
            amc_expiry_str = p.get("amc_expiry", "").strip()
            uptime_val = float(p.get("uptime_guarantee_percent") or 95.0)
            penalty_val = float(p.get("penalty_rate_per_day") or 0.0)
            camc_val = float(p.get("annual_camc_cost") or 0.0)
            if not centre_id or not name:
                messages.error(request, "Centre and equipment name are required.")
                return redirect(back)
            centre = get_object_or_404(Centre, pk=centre_id)
            vendor = Vendor.objects.filter(pk=vendor_id).first() if vendor_id else None
            amc_date = None
            if amc_expiry_str:
                try:
                    amc_date = datetime.strptime(amc_expiry_str, "%Y-%m-%d").date()
                except ValueError:
                    pass
            eq = Equipment.objects.create(
                centre=centre, name=name, make=make, model=model, serial_no=serial_no,
                vendor=vendor, amc_expiry=amc_date, uptime_guarantee_percent=uptime_val,
                penalty_rate_per_day=penalty_val, annual_camc_cost=camc_val
            )
            messages.success(request, f"Equipment '{eq.name}' ({centre.code}) created.")
        elif do == "edit":
            eq = get_object_or_404(Equipment, pk=p.get("id") or 0)
            centre_id = p.get("centre") or eq.centre_id
            name = p.get("name", "").strip()
            make = p.get("make", "").strip()
            model = p.get("model", "").strip()
            serial_no = p.get("serial_no", "").strip()
            vendor_id = p.get("vendor") or None
            amc_expiry_str = p.get("amc_expiry", "").strip()
            uptime_val = float(p.get("uptime_guarantee_percent") or 95.0)
            penalty_val = float(p.get("penalty_rate_per_day") or 0.0)
            camc_val = float(p.get("annual_camc_cost") or 0.0)
            active = p.get("active") == "on"
            if not name:
                messages.error(request, "Equipment name is required.")
                return redirect(back)
            centre = get_object_or_404(Centre, pk=centre_id)
            vendor = Vendor.objects.filter(pk=vendor_id).first() if vendor_id else None
            amc_date = None
            if amc_expiry_str:
                try:
                    amc_date = datetime.strptime(amc_expiry_str, "%Y-%m-%d").date()
                except ValueError:
                    pass
            eq.centre = centre
            eq.name = name
            eq.make = make
            eq.model = model
            eq.serial_no = serial_no
            eq.vendor = vendor
            eq.amc_expiry = amc_date
            eq.uptime_guarantee_percent = uptime_val
            eq.penalty_rate_per_day = penalty_val
            eq.annual_camc_cost = camc_val
            eq.active = active
            eq.save()
            messages.success(request, f"Equipment '{eq.name}' updated.")

    elif section == "pm":
        if do == "add":
            eq_id = p.get("equipment") or None
            title = p.get("title", "").strip()
            freq = int(p.get("frequency_months") or 3)
            next_due_str = p.get("next_due_date", "").strip()
            notice = int(p.get("advance_notice_days") or 7)
            assigned_id = p.get("assigned_to") or None
            notes = p.get("notes", "").strip()
            if not eq_id or not title or not next_due_str:
                messages.error(request, "Equipment, title, and target due date are required.")
                return redirect(back)
            eq = get_object_or_404(Equipment, pk=eq_id)
            try:
                next_due = datetime.strptime(next_due_str, "%Y-%m-%d").date()
            except ValueError:
                messages.error(request, "Invalid due date.")
                return redirect(back)
            assignee = User.objects.filter(pk=assigned_id, is_active=True).first() if assigned_id else None
            pm = PreventiveMaintenance.objects.create(
                equipment=eq, title=title, frequency_months=freq, next_due_date=next_due,
                advance_notice_days=notice, assigned_to=assignee, notes=notes
            )
            messages.success(request, f"PM schedule '{pm.title}' added for {eq.name}.")
        elif do == "edit":
            pm = get_object_or_404(PreventiveMaintenance, pk=p.get("id") or 0)
            eq_id = p.get("equipment") or pm.equipment_id
            title = p.get("title", "").strip()
            freq = int(p.get("frequency_months") or 3)
            next_due_str = p.get("next_due_date", "").strip()
            notice = int(p.get("advance_notice_days") or 7)
            assigned_id = p.get("assigned_to") or None
            notes = p.get("notes", "").strip()
            active = p.get("active") == "on"
            if not title or not next_due_str:
                messages.error(request, "Title and target due date are required.")
                return redirect(back)
            try:
                next_due = datetime.strptime(next_due_str, "%Y-%m-%d").date()
            except ValueError:
                messages.error(request, "Invalid due date.")
                return redirect(back)
            pm.equipment_id = eq_id
            pm.title = title
            pm.frequency_months = freq
            pm.next_due_date = next_due
            pm.advance_notice_days = notice
            pm.assigned_to_id = assigned_id
            pm.notes = notes
            pm.active = active
            pm.save()
            messages.success(request, f"PM schedule '{pm.title}' updated.")
        elif do == "toggle":
            pm = get_object_or_404(PreventiveMaintenance, pk=p.get("id") or 0)
            pm.active = not pm.active
            pm.save()
            messages.success(request, f"PM schedule '{pm.title}' {'activated' if pm.active else 'deactivated'}.")

    return redirect(back)

