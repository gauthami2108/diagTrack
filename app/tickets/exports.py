"""
Reports by centre, department, category, priority, owner or equipment.

build_report() works out the figures shown on the Reports screen; to_excel() turns the
same figures into a workbook. Every figure is about tickets RAISED in the chosen period,
limited to what the signed-in person is allowed to see.
"""
import io
from calendar import monthrange
from collections import OrderedDict
from datetime import date, datetime, time, timedelta

from django.utils import timezone

from . import services
from .models import OPEN_STATUSES, Centre, Department, DowntimeLog, Priority, Status, VendorCall

GROUPS = OrderedDict([
    ("centre", "Centre"),
    ("department", "Department"),
    ("centre_department", "Centre and department"),
    ("category", "Category"),
    ("priority", "Priority"),
    ("owner", "Owner"),
    ("equipment", "Equipment"),
])
PERIODS = OrderedDict([
    ("7", "Last 7 days"), ("30", "Last 30 days"), ("90", "Last 90 days"),
    ("month", "This month"), ("lastmonth", "Last month"), ("custom", "Choose dates"),
])
DONE = (Status.RESOLVED, Status.CLOSED)


def _ints(values):
    return [int(v) for v in values if str(v).isdigit()]


def period_bounds(params, today=None):
    """Turn the chosen period into a first and last day (both included)."""
    today = today or timezone.localdate()
    p = params.get("period") or "30"
    if p == "month":
        return today.replace(day=1), today, p
    if p == "lastmonth":
        last = today.replace(day=1) - timedelta(days=1)
        return last.replace(day=1), last.replace(day=monthrange(last.year, last.month)[1]), p
    if p == "custom":
        try:
            start = date.fromisoformat(params.get("from") or "")
            end = date.fromisoformat(params.get("to") or "")
            if start > end:
                start, end = end, start
            return start, min(end, today), p
        except ValueError:
            p = "30"
    days = int(p) if p in ("7", "30", "90") else 30
    return today - timedelta(days=days - 1), today, p if p in PERIODS else "30"


def _key(t, group):
    if group == "centre":
        return (t.centre.name,), (t.centre.name,)
    if group == "department":
        return (t.department.name,), (t.department.name,)
    if group == "centre_department":
        return (t.centre.name, t.department.name), (t.centre.name, t.department.name)
    if group == "category":
        return (t.department.name, t.category.name), (t.category.name, t.department.name)
    if group == "priority":
        return (t.priority,), (t.get_priority_display(),)
    if group == "owner":
        name = t.assignee.display_name if t.assignee else "Unassigned"
        return ((1 if not t.assignee else 0), name), (name,)
    if t.equipment:
        return (t.equipment.centre.name, t.equipment.name), (t.equipment.name, t.equipment.centre.name)
    return ("~",), ("No equipment",)


GROUP_COLUMNS = {
    "centre": ["Centre"], "department": ["Department"], "centre_department": ["Centre", "Department"],
    "category": ["Category", "Department"], "priority": ["Priority"], "owner": ["Owner"],
    "equipment": ["Equipment", "Centre"],
}


def _metrics(tickets, now):
    raised = len(tickets)
    done = [t for t in tickets if t.status in DONE]
    open_ = [t for t in tickets if t.status in OPEN_STATUSES]
    late = [t for t in tickets if t.resolution_breached or
            (t.status in OPEN_STATUSES and t.status != Status.ON_HOLD and t.resolution_due < now)]
    met = [t for t in done if not t.resolution_breached]
    responded = [t for t in tickets if t.first_response_at]
    resp_met = [t for t in responded if not t.response_breached]
    hours = [((t.resolved_at - t.created_at).total_seconds() - t.hold_seconds) / 3600 for t in done if t.resolved_at]
    ratings = [t.rating for t in done if t.rating]
    pct = lambda a, b: round(100 * len(a) / len(b)) if b else None
    return {
        "raised": raised, "resolved": len(done), "open": len(open_), "late": len(late),
        "p1": sum(1 for t in tickets if t.priority == Priority.P1),
        "sla_pct": pct(met, done), "resp_pct": pct(resp_met, responded),
        "avg_hours": round(sum(hours) / len(hours), 1) if hours else None,
        "rating": round(sum(ratings) / len(ratings), 1) if ratings else None,
    }


def band(pct):
    if pct is None:
        return "none"
    return "good" if pct >= 90 else "fair" if pct >= 75 else "bad"


def build_report(user, params, now=None):
    now = now or timezone.now()
    start, end, period = period_bounds(params)
    tz = timezone.get_current_timezone()
    since = timezone.make_aware(datetime.combine(start, time.min), tz)
    until = timezone.make_aware(datetime.combine(end + timedelta(days=1), time.min), tz)
    group = params.get("group") if params.get("group") in GROUPS else "centre"
    centre_ids = _ints(params.getlist("centre")) if hasattr(params, "getlist") else []
    dept_ids = _ints(params.getlist("department")) if hasattr(params, "getlist") else []
    prios = [p for p in (params.getlist("priority") if hasattr(params, "getlist") else []) if p in Priority.values]
    state = params.get("state") if params.get("state") in ("open", "done") else "any"

    qs = services.visible_tickets(user).filter(created_at__gte=since, created_at__lt=until)
    if centre_ids:
        qs = qs.filter(centre_id__in=centre_ids)
    if dept_ids:
        qs = qs.filter(department_id__in=dept_ids)
    if prios:
        qs = qs.filter(priority__in=prios)
    if state == "open":
        qs = qs.filter(status__in=OPEN_STATUSES)
    elif state == "done":
        qs = qs.exclude(status__in=OPEN_STATUSES)
    tickets = list(qs.select_related("equipment__centre").order_by("created_at"))

    buckets = {}
    for t in tickets:
        sort_key, labels = _key(t, group)
        buckets.setdefault(sort_key, (labels, []))[1].append(t)
    rows = []
    for sort_key in sorted(buckets):
        labels, items = buckets[sort_key]
        m = _metrics(items, now)
        m.update(labels=labels, sla_band=band(m["sla_pct"]), resp_band=band(m["resp_pct"]))
        rows.append(m)
    totals = _metrics(tickets, now)
    totals.update(sla_band=band(totals["sla_pct"]), resp_band=band(totals["resp_pct"]))

    # centre by department grid of tickets raised
    centres = sorted({t.centre for t in tickets}, key=lambda c: c.name)
    depts = sorted({t.department for t in tickets}, key=lambda d: d.name)
    counts = {}
    for t in tickets:
        counts[(t.centre_id, t.department_id)] = counts.get((t.centre_id, t.department_id), 0) + 1
    pivot = [{"centre": c, "cells": [counts.get((c.pk, d.pk), 0) for d in depts],
              "total": sum(counts.get((c.pk, d.pk), 0) for d in depts)} for c in centres]
    pivot_totals = [sum(counts.get((c.pk, d.pk), 0) for c in centres) for d in depts]

    breaches = [t for t in tickets if t.response_breached or t.resolution_breached or
                (t.status in OPEN_STATUSES and t.status != Status.ON_HOLD and t.resolution_due < now)]

    downtime = DowntimeLog.objects.filter(ticket__in=[t.pk for t in tickets]).select_related(
        "equipment__centre", "ticket")
    calls = VendorCall.objects.filter(ticket__in=[t.pk for t in tickets]).select_related("vendor", "ticket")

    names = lambda model, ids: ", ".join(model.objects.filter(pk__in=ids).values_list("name", flat=True))
    return {
        "start": start, "end": end, "period": period, "period_label": PERIODS.get(period, ""),
        "group": group, "group_label": GROUPS[group], "group_columns": GROUP_COLUMNS[group],
        "state": state, "tickets": tickets, "rows": rows, "totals": totals,
        "pivot": pivot, "pivot_depts": depts, "pivot_totals": pivot_totals,
        "breaches": breaches, "downtime": list(downtime), "calls": list(calls),
        "filters": {
            "Centres": names(Centre, centre_ids) or "All you can see",
            "Departments": names(Department, dept_ids) or "All",
            "Priorities": ", ".join(prios) or "All",
            "Tickets": {"any": "All", "open": "Still open", "done": "Finished"}[state],
        },
        "selected": {"centre": centre_ids, "department": dept_ids, "priority": prios},
        "generated": timezone.localtime(now),
    }


# --------------------------------------------------------------------------
# Excel
# --------------------------------------------------------------------------
def to_excel(r, org_name, author, brand="274A99"):
    from openpyxl import Workbook
    from openpyxl.formatting.rule import CellIsRule
    from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
    from openpyxl.utils import get_column_letter

    brand = brand.lstrip("#").upper()
    head_font = Font(bold=True, color="FFFFFF")
    head_fill = PatternFill("solid", fgColor=brand)
    total_fill = PatternFill("solid", fgColor="EEF2F7")
    thin = Side(style="thin", color="DDE4EC")
    good = PatternFill("solid", fgColor="E0F3EA")
    fair = PatternFill("solid", fgColor="FCF0D7")
    bad = PatternFill("solid", fgColor="FAE5E3")
    fmt_dt = "dd mmm yyyy hh:mm"
    local = lambda d: timezone.localtime(d).replace(tzinfo=None) if d else None

    wb = Workbook()

    def sheet(ws, title_lines, headers, rows, widths, pct_cols=(), num_cols=(), date_cols=(), total=None):
        for i, line in enumerate(title_lines, 1):
            ws.cell(row=i, column=1, value=line).font = Font(bold=(i == 1), size=14 if i == 1 else 10,
                                                            color="0E1C2E" if i == 1 else "44566C")
        top = len(title_lines) + 2
        for c, h in enumerate(headers, 1):
            cell = ws.cell(row=top, column=c, value=h)
            cell.font, cell.fill = head_font, head_fill
            cell.alignment = Alignment(vertical="center", wrap_text=True)
        for r_i, row in enumerate(rows, top + 1):
            for c, v in enumerate(row, 1):
                cell = ws.cell(row=r_i, column=c, value=v)
                cell.border = Border(bottom=thin)
                if c in pct_cols and v is not None:
                    cell.number_format = '0"%"'
                if c in num_cols and v is not None:
                    cell.number_format = "0.0"
                if c in date_cols and v is not None:
                    cell.number_format = fmt_dt
        last = top + len(rows)
        if total:
            last += 1
            for c, v in enumerate(total, 1):
                cell = ws.cell(row=last, column=c, value=v)
                cell.font, cell.fill = Font(bold=True), total_fill
                if c in pct_cols and v is not None:
                    cell.number_format = '0"%"'
                if c in num_cols and v is not None:
                    cell.number_format = "0.0"
        for c in pct_cols:
            col = get_column_letter(c)
            rng = f"{col}{top + 1}:{col}{max(last, top + 1)}"
            ws.conditional_formatting.add(rng, CellIsRule(operator="greaterThanOrEqual", formula=["90"], fill=good))
            ws.conditional_formatting.add(rng, CellIsRule(operator="between", formula=["75", "89.999"], fill=fair))
            ws.conditional_formatting.add(rng, CellIsRule(operator="lessThan", formula=["75"], fill=bad))
        for c, w in enumerate(widths, 1):
            ws.column_dimensions[get_column_letter(c)].width = w
        ws.row_dimensions[top].height = 30
        ws.freeze_panes = ws.cell(row=top + 1, column=1)
        if rows:
            ws.auto_filter.ref = f"A{top}:{get_column_letter(len(headers))}{top + len(rows)}"
        ws.sheet_view.showGridLines = False
        ws.page_setup.orientation = "landscape"
        ws.page_setup.fitToWidth = 1
        ws.sheet_properties.pageSetUpPr.fitToPage = True
        ws.page_setup.fitToHeight = 0

    period = f"{r['start']:%d %b %Y} to {r['end']:%d %b %Y}"
    about = [f"{org_name}: tickets by {r['group_label'].lower()}",
             f"Tickets raised {period}. " + ". ".join(f"{k}: {v}" for k, v in r["filters"].items()) + ".",
             f"Prepared by {author} on {r['generated']:%d %b %Y at %H:%M}. Resolved in time and responded in time: "
             "90% or more is on target, 75 to 89% watch, under 75% below target."]
    gcols = r["group_columns"]
    metric_heads = ["Raised", "Resolved", "Still open", "Past deadline", "P1", "Resolved in time",
                    "Responded in time", "Average hours to resolve", "Average rating"]
    def metric_vals(m):
        return [m["raised"], m["resolved"], m["open"], m["late"], m["p1"], m["sla_pct"], m["resp_pct"],
                m["avg_hours"], m["rating"]]
    n = len(gcols)
    ws = wb.active
    ws.title = "Summary"
    sheet(ws, about, gcols + metric_heads,
          [list(m["labels"]) + metric_vals(m) for m in r["rows"]],
          [28] * n + [10, 11, 11, 13, 7, 14, 15, 15, 12],
          pct_cols=(n + 6, n + 7), num_cols=(n + 8, n + 9),
          total=["All"] + [""] * (n - 1) + metric_vals(r["totals"]))

    ws = wb.create_sheet("Centre x Department")
    depts = [d.name for d in r["pivot_depts"]]
    sheet(ws, [f"{org_name}: tickets raised by centre and department", f"Tickets raised {period}."],
          ["Centre"] + depts + ["Total"],
          [[p["centre"].name] + p["cells"] + [p["total"]] for p in r["pivot"]],
          [26] + [14] * len(depts) + [10],
          total=["Total"] + r["pivot_totals"] + [sum(r["pivot_totals"])])

    ws = wb.create_sheet("Tickets")
    sheet(ws, [f"{org_name}: every ticket in this report", f"Tickets raised {period}."],
          ["Ticket", "Raised", "Centre", "Department", "Category", "Title", "Priority", "Status", "Raised by",
           "Owner", "Equipment", "First response", "Resolve by", "Resolved", "Hours to resolve",
           "Response missed", "Resolution missed", "Rating"],
          [[t.number, local(t.created_at), t.centre.name, t.department.name, t.category.name, t.subject, t.priority,
            t.get_status_display(), t.requester.display_name, t.assignee.display_name if t.assignee else "",
            t.equipment.name if t.equipment else "", local(t.first_response_at), local(t.resolution_due),
            local(t.resolved_at),
            round(((t.resolved_at - t.created_at).total_seconds() - t.hold_seconds) / 3600, 1) if t.resolved_at else None,
            "Yes" if t.response_breached else "No", "Yes" if t.resolution_breached else "No", t.rating]
           for t in r["tickets"]],
          [14, 17, 18, 18, 22, 40, 9, 12, 18, 18, 18, 17, 17, 17, 10, 10, 10, 8],
          num_cols=(15,), date_cols=(2, 12, 13, 14))

    ws = wb.create_sheet("Missed deadlines")
    sheet(ws, [f"{org_name}: tickets that missed or are past a deadline", f"Tickets raised {period}."],
          ["Ticket", "Raised", "Centre", "Department", "Title", "Priority", "Status", "Owner", "Response missed",
           "Resolution missed or overdue", "Resolve by"],
          [[t.number, local(t.created_at), t.centre.name, t.department.name, t.subject, t.priority,
            t.get_status_display(), t.assignee.display_name if t.assignee else "Unassigned",
            "Yes" if t.response_breached else "No",
            "Yes" if (t.resolution_breached or (t.status in OPEN_STATUSES and t.resolution_due < timezone.now()))
            else "No",
            local(t.resolution_due)] for t in r["breaches"]],
          [14, 17, 18, 18, 40, 9, 12, 18, 10, 14, 17], date_cols=(2, 11))

    ws = wb.create_sheet("Equipment downtime")
    sheet(ws, [f"{org_name}: equipment downtime from these tickets", f"Tickets raised {period}."],
          ["Equipment", "Centre", "Ticket", "Down from", "Back at", "Hours down"],
          [[d.equipment.name, d.equipment.centre.name, d.ticket.number, local(d.start), local(d.end),
            round(d.seconds() / 3600, 1)] for d in r["downtime"]],
          [24, 18, 14, 17, 17, 11], num_cols=(6,), date_cols=(4, 5))

    ws = wb.create_sheet("Vendor calls")
    sheet(ws, [f"{org_name}: vendor calls on these tickets", f"Tickets raised {period}."],
          ["Vendor", "Ticket", "Complaint number", "Called", "Engineer arrived", "Engineer", "Hours to arrive"],
          [[c.vendor.name, c.ticket.number, c.reference, local(c.called_at), local(c.attended_at), c.engineer,
            round(c.response_seconds() / 3600, 1) if c.attended_at else None] for c in r["calls"]],
          [24, 14, 16, 17, 17, 18, 12], num_cols=(7,), date_cols=(4, 5))

    wb.properties.creator = author
    wb.properties.title = about[0]
    out = io.BytesIO()
    wb.save(out)
    return out.getvalue()
