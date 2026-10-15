"""Numbers for the home page and the dashboard. All counts respect what the person may see."""
from datetime import datetime, timedelta

from django.db.models import Count, Q
from django.utils import timezone

from .models import OPEN_STATUSES, DowntimeLog, Status


def _bars(rows, key):
    """rows: [(label, value, filter value)] -> list of dicts with a width percent for drawing."""
    top = max([r[1] for r in rows] or [1]) or 1
    return [{"label": r[0], "value": r[1], "pct": max(2, round(100 * r[1] / top)) if r[1] else 0, "param": key, "id": r[2]} for r in rows]


def headline(qs, now=None):
    now = now or timezone.now()
    open_qs = qs.filter(status__in=OPEN_STATUSES)
    running = open_qs.exclude(status=Status.ON_HOLD)
    return {
        "open": open_qs.count(),
        "breached": open_qs.filter(Q(resolution_breached=True) | Q(resolution_due__lt=now)).exclude(
            status=Status.ON_HOLD).count(),
        "due_soon": running.filter(resolution_due__gte=now, resolution_due__lt=now + timedelta(hours=4)).count(),
        "unassigned": open_qs.filter(assignee__isnull=True).count(),
        "on_hold": open_qs.filter(status=Status.ON_HOLD).count(),
        "to_confirm": qs.filter(status=Status.RESOLVED).count(),
    }


def dashboard(qs, days=30, now=None):
    now = now or timezone.now()
    since = now - timedelta(days=days)
    open_qs = qs.filter(status__in=OPEN_STATUSES)
    period = qs.filter(created_at__gte=since)

    def grouped(base, field, label, key, limit=8):
        rows = (base.order_by().values(field, label).annotate(n=Count("id")).order_by("-n")[:limit])
        return _bars([(r[label], r["n"], r[field]) for r in rows], key)

    labels = dict(Status.choices)
    status_counts = dict(open_qs.order_by().values_list("status").annotate(n=Count("id")))
    by_status = _bars([(labels[s], status_counts.get(s, 0), s) for s in OPEN_STATUSES], "status")

    done = period.filter(status__in=[Status.RESOLVED, Status.CLOSED])
    done_n = done.count()
    met = done.filter(resolution_breached=False).count()
    responded = period.filter(first_response_at__isnull=False)
    resp_n = responded.count()
    resp_met = responded.filter(response_breached=False).count()

    ages = [("Under 1 day", 0, 1), ("1 to 3 days", 1, 3), ("3 to 7 days", 3, 7), ("Over 7 days", 7, 3650)]
    ageing = _bars([(name, open_qs.filter(created_at__lte=now - timedelta(days=lo),
                                          created_at__gt=now - timedelta(days=hi)).count(), "")
                    for name, lo, hi in ages], "")

    # daily raised vs resolved for the last 14 days
    trend = []
    today = timezone.localtime(now).replace(hour=0, minute=0, second=0, microsecond=0)
    for back in range(13, -1, -1):
        start = today - timedelta(days=back)
        end = start + timedelta(days=1)
        trend.append({"day": start, "raised": qs.filter(created_at__gte=start, created_at__lt=end).count(),
                      "resolved": qs.filter(resolved_at__gte=start, resolved_at__lt=end).count()})

    priorities = dict(open_qs.order_by().values_list("priority").annotate(n=Count("id")))
    return {
        "days": days,
        "head": headline(qs, now),
        "raised": period.count(),
        "resolved": done_n,
        "sla_pct": round(100 * met / done_n) if done_n else None,
        "sla_met": met, "sla_total": done_n,
        "resp_pct": round(100 * resp_met / resp_n) if resp_n else None,
        "by_status": by_status,
        "by_centre": grouped(open_qs, "centre", "centre__name", "centre"),
        "by_department": grouped(open_qs, "department", "department__name", "department"),
        "by_category": grouped(period, "category", "category__name", "category"),
        "by_priority": _bars([(p, priorities.get(p, 0), p) for p in ("P1", "P2", "P3", "P4")], "priority"),
        "ageing": ageing,
        "trend": trend_chart(trend),
    }


def trend_chart(points, width=1000, height=240, pad_l=30, pad_b=26, pad_t=12):
    """Pre-computes SVG coordinates so the template only has to draw them."""
    top = max([max(p["raised"], p["resolved"]) for p in points] + [4])
    top = top + (-top % 4)  # round up to a multiple of 4 for clean grid lines
    plot_w, plot_h = width - pad_l - 8, height - pad_b - pad_t
    step = plot_w / max(1, len(points) - 1)

    def y(v):
        return round(pad_t + plot_h * (1 - v / top), 1)

    for i, p in enumerate(points):
        p["x"] = round(pad_l + i * step, 1)
        p["y_raised"], p["y_resolved"] = y(p["raised"]), y(p["resolved"])
        p["hit_x"] = round(p["x"] - step / 2, 1)
        p["label"] = i % 2 == 1 or i == len(points) - 1
    return {
        "points": points, "width": width, "height": height, "step": round(step, 1),
        "raised_path": " ".join(f"{p['x']},{p['y_raised']}" for p in points),
        "resolved_path": " ".join(f"{p['x']},{p['y_resolved']}" for p in points),
        "grid": [{"v": int(top * k / 4), "y": y(top * k / 4)} for k in range(5)],
        "pad_l": pad_l, "base_y": y(0), "plot_h": plot_h, "pad_t": pad_t,
    }


def equipment_uptime(equipment, days=30, now=None):
    now = now or timezone.now()
    since = now - timedelta(days=days)
    logs = DowntimeLog.objects.filter(equipment=equipment).filter(Q(end__isnull=True) | Q(end__gte=since))
    down = sum(d.seconds(now, since) for d in logs)
    total = days * 86400
    return {"down_seconds": down, "uptime_pct": round(100 * (1 - min(down, total) / total), 1),
            "is_down": DowntimeLog.objects.filter(equipment=equipment, end__isnull=True).exists()}


# --------------------------------------------------------------------------
# Centre matrix
# --------------------------------------------------------------------------
def _band(pct, good=90, fair=75):
    if pct is None:
        return "none"
    return "good" if pct >= good else "fair" if pct >= fair else "bad"


def _hours(seconds):
    if seconds is None:
        return None
    return round(seconds / 3600, 1)


def centre_matrix(qs, centres, days=30, now=None):
    """One row per centre: load, SLA performance, speed and equipment health."""
    from .models import Centre, Department, Equipment  # local to keep imports light
    now = now or timezone.now()
    since = now - timedelta(days=days)
    rows = []
    for c in centres:
        cq = qs.filter(centre=c)
        open_q = cq.filter(status__in=OPEN_STATUSES)
        period = cq.filter(created_at__gte=since)
        done = list(period.filter(status__in=[Status.RESOLVED, Status.CLOSED]).values_list(
            "created_at", "resolved_at", "hold_seconds", "resolution_breached", "rating"))
        met = sum(1 for d in done if not d[3])
        durations = [(d[1] - d[0]).total_seconds() - d[2] for d in done if d[1]]
        ratings = [d[4] for d in done if d[4]]
        responded = period.filter(first_response_at__isnull=False)
        resp_n = responded.count()
        resp_met = responded.filter(response_breached=False).count()
        machines = list(Equipment.objects.filter(centre=c, active=True))
        ups = [equipment_uptime(e, days, now) for e in machines]
        row = {
            "centre": c,
            "open": open_q.count(),
            "p1": open_q.filter(priority="P1").count(),
            "late": open_q.filter(Q(resolution_breached=True) | Q(resolution_due__lt=now)).exclude(
                status=Status.ON_HOLD).count(),
            "raised": period.count(),
            "resolved": len(done),
            "sla_pct": round(100 * met / len(done)) if done else None,
            "resp_pct": round(100 * resp_met / resp_n) if resp_n else None,
            "avg_hours": _hours(sum(durations) / len(durations)) if durations else None,
            "rating": round(sum(ratings) / len(ratings), 1) if ratings else None,
            "machines": len(machines),
            "down": sum(1 for u in ups if u["is_down"]),
            "uptime": round(sum(u["uptime_pct"] for u in ups) / len(ups), 1) if ups else None,
        }
        row["sla_band"] = _band(row["sla_pct"])
        row["resp_band"] = _band(row["resp_pct"])
        row["uptime_band"] = _band(row["uptime"], 98, 95)
        rows.append(row)

    # centre x department grid of open tickets
    depts = list(Department.objects.filter(active=True))
    counts = {(a, b): n for a, b, n in qs.filter(status__in=OPEN_STATUSES).order_by()
              .values_list("centre", "department").annotate(n=Count("id"))}
    top = max(counts.values() or [1]) or 1
    grid = [{"centre": r["centre"], "cells": [
        {"dept": d, "n": counts.get((r["centre"].pk, d.pk), 0),
         "level": 0 if not counts.get((r["centre"].pk, d.pk)) else
         min(4, 1 + int(3 * counts[(r["centre"].pk, d.pk)] / top))} for d in depts]} for r in rows]
    # the same figures turned round: one row per department, one narrow column per centre code
    dept_grid = [{"dept": d, "cells": [dict(g["cells"][i], centre=g["centre"]) for g in grid]}
                 for i, d in enumerate(depts)]

    total_done = sum(r["resolved"] for r in rows)
    weighted = sum((r["sla_pct"] or 0) * r["resolved"] for r in rows)
    return {
        "rows": rows, "depts": depts, "grid": grid, "dept_grid": dept_grid, "days": days,
        "totals": {
            "open": sum(r["open"] for r in rows), "p1": sum(r["p1"] for r in rows),
            "late": sum(r["late"] for r in rows), "raised": sum(r["raised"] for r in rows),
            "resolved": total_done, "down": sum(r["down"] for r in rows),
            "sla_pct": round(weighted / total_done) if total_done else None,
        },
        "sla_bars": sorted([r for r in rows if r["sla_pct"] is not None], key=lambda r: r["sla_pct"]),
    }


# --------------------------------------------------------------------------
# Equipment pack
# --------------------------------------------------------------------------
def repeat_faults(qs, days=90, minimum=3, now=None):
    """Machines with several tickets in the period: candidates for a root-cause review."""
    from .models import Equipment
    now = now or timezone.now()
    rows = (qs.filter(equipment__isnull=False, created_at__gte=now - timedelta(days=days)).order_by()
            .values("equipment").annotate(n=Count("id")).filter(n__gte=minimum).order_by("-n"))
    found = {e.pk: e for e in Equipment.objects.filter(pk__in=[r["equipment"] for r in rows]).select_related("centre")}
    return [{"equipment": found[r["equipment"]], "n": r["n"]} for r in rows if r["equipment"] in found]


def vendor_performance(days=90, now=None, centres=None):
    """Calls, how many were attended, and the average time until the engineer arrived, per vendor."""
    from .models import VendorCall
    now = now or timezone.now()
    calls = VendorCall.objects.filter(called_at__gte=now - timedelta(days=days)).select_related("vendor")
    if centres is not None:
        calls = calls.filter(ticket__centre__in=centres)
    by = {}
    for c in calls:
        v = by.setdefault(c.vendor_id, {"vendor": c.vendor, "calls": 0, "attended": 0, "waiting": 0, "secs": []})
        v["calls"] += 1
        if c.attended_at:
            v["attended"] += 1
            v["secs"].append(c.response_seconds())
        else:
            v["waiting"] += 1
    out = []
    for v in by.values():
        v["avg_hours"] = _hours(sum(v["secs"]) / len(v["secs"])) if v["secs"] else None
        out.append(v)
    return sorted(out, key=lambda v: -(v["avg_hours"] or 0))


def amc_due(centres=None, days=60, today=None):
    from .models import Equipment
    today = today or timezone.localdate()
    qs = Equipment.objects.filter(active=True, amc_expiry__isnull=False,
                                  amc_expiry__lte=today + timedelta(days=days)).select_related("centre", "vendor")
    if centres is not None:
        qs = qs.filter(centre__in=centres)
    out = []
    for e in qs.order_by("amc_expiry"):
        e.days_left = (e.amc_expiry - today).days
        out.append(e)
    return out


def pm_schedules_due(centres=None, today=None):
    from .models import PreventiveMaintenance
    today = today or timezone.localdate()
    try:
        qs = PreventiveMaintenance.objects.filter(
            active=True, equipment__active=True
        ).select_related("equipment__centre", "equipment__vendor", "assigned_to", "last_ticket")
        if centres is not None:
            qs = qs.filter(equipment__centre__in=centres)
        out = []
        for pm in qs.order_by("next_due_date", "equipment__name"):
            days_left = (pm.next_due_date - today).days
            pm.days_left = days_left
            pm.days_overdue = abs(days_left) if days_left < 0 else 0
            if days_left < 0:
                pm.due_status = "overdue"
            elif days_left <= pm.advance_notice_days:
                pm.due_status = "due_soon"
            else:
                pm.due_status = "scheduled"
            out.append(pm)
        return out
    except Exception:
        return []


def camc_uptime_penalties(equipment=None, centres=None, days=90, now=None):
    """
    Vendor CAMC Uptime Guarantee & Penalty Calculator.
    Compares actual machine downtime against contractual uptime guarantees over a window (default 90 days),
    and computes financial penalty deductions based on daily rates or pro-rata annual CAMC cost.
    """
    from .models import Equipment, DowntimeLog
    now = now or timezone.now()
    since = now - timedelta(days=days)
    total_hours = round(days * 24.0, 1)
    total_seconds = days * 86400

    if equipment is not None:
        qs = Equipment.objects.filter(pk=equipment.pk).select_related("centre", "vendor")
    else:
        qs = Equipment.objects.filter(active=True).select_related("centre", "vendor")
        if centres is not None:
            qs = qs.filter(centre__in=centres)

    rows = []
    for e in qs.order_by("centre__name", "name"):
        logs = DowntimeLog.objects.filter(equipment=e).filter(Q(end__isnull=True) | Q(end__gte=since))
        down_secs = sum(d.seconds(now, since) for d in logs)
        down_hours = round(down_secs / 3600.0, 1)
        uptime_pct = round(100.0 * (1.0 - min(down_secs, total_seconds) / total_seconds), 2)

        guarantee_val = getattr(e, "uptime_guarantee_percent", None)
        guarantee_pct = float(guarantee_val if guarantee_val is not None else 95.0)

        allowable_down_hours = round(total_hours * (1.0 - guarantee_pct / 100.0), 1)
        excess_down_hours = max(0.0, round(down_hours - allowable_down_hours, 1))
        excess_days = round(excess_down_hours / 24.0, 2)

        penalty_rate = float(getattr(e, "penalty_rate_per_day", 0.0) or 0.0)
        camc_cost = float(getattr(e, "annual_camc_cost", 0.0) or 0.0)

        if penalty_rate > 0:
            penalty_amount = round(excess_days * penalty_rate, 2)
            calc_basis = f"₹{penalty_rate:,.0f} / day excess"
        elif camc_cost > 0:
            penalty_amount = round((excess_down_hours / (365.0 * 24.0)) * camc_cost, 2)
            calc_basis = f"Pro-rata of ₹{camc_cost:,.0f} CAMC"
        else:
            penalty_amount = 0.0
            calc_basis = "No penalty rate configured"

        if excess_down_hours > 0:
            status = "penalty"
            status_label = "Penalty Applicable"
        elif allowable_down_hours > 0 and down_hours >= 0.75 * allowable_down_hours:
            status = "warning"
            status_label = "At Risk"
        else:
            status = "compliant"
            status_label = "Compliant"

        rows.append({
            "equipment": e,
            "vendor": e.vendor,
            "centre": e.centre,
            "guarantee_pct": guarantee_pct,
            "uptime_pct": uptime_pct,
            "down_hours": down_hours,
            "allowable_down_hours": allowable_down_hours,
            "excess_down_hours": excess_down_hours,
            "excess_days": excess_days,
            "penalty_rate": penalty_rate,
            "camc_cost": camc_cost,
            "penalty_amount": penalty_amount,
            "calc_basis": calc_basis,
            "status": status,
            "status_label": status_label,
            "is_down": logs.filter(end__isnull=True).exists(),
        })

    total_machines = len(rows)
    penalized = sum(1 for r in rows if r["status"] == "penalty")
    at_risk = sum(1 for r in rows if r["status"] == "warning")
    total_penalty = sum(r["penalty_amount"] for r in rows)
    total_excess_hours = round(sum(r["excess_down_hours"] for r in rows), 1)
    avg_uptime = round(sum(r["uptime_pct"] for r in rows) / total_machines, 2) if total_machines else 100.0

    return {
        "rows": rows,
        "days": days,
        "total_hours": total_hours,
        "summary": {
            "total_machines": total_machines,
            "penalized": penalized,
            "at_risk": at_risk,
            "compliant": total_machines - penalized - at_risk,
            "total_penalty": total_penalty,
            "total_excess_hours": total_excess_hours,
            "avg_uptime": avg_uptime,
        }
    }


# --------------------------------------------------------------------------
# Weekly Management Summary Report
# --------------------------------------------------------------------------
def generate_weekly_management_summary(start_date=None, end_date=None):
    from .models import Ticket, DowntimeLog, Centre, Priority, OPEN_STATUSES
    now = timezone.now()
    if not end_date:
        end_date = timezone.localdate(now)
    if not start_date:
        start_date = end_date - timedelta(days=7)

    start_dt = timezone.make_aware(datetime.combine(start_date, datetime.min.time())) if timezone.is_naive(datetime.combine(start_date, datetime.min.time())) else datetime.combine(start_date, datetime.min.time())
    end_dt = timezone.make_aware(datetime.combine(end_date, datetime.max.time())) if timezone.is_naive(datetime.combine(end_date, datetime.max.time())) else datetime.combine(end_date, datetime.max.time())

    # 1. Missed Deadlines
    breached_tickets = Ticket.objects.filter(
        created_at__gte=start_dt,
        created_at__lte=end_dt
    ).filter(
        Q(response_breached=True) | Q(resolution_breached=True)
    ).select_related("centre", "department", "assignee", "equipment").order_by("-priority", "-created_at")

    # 2. Machine Downtime
    downtimes = DowntimeLog.objects.filter(
        start__gte=start_dt,
        start__lte=end_dt
    ).select_related("equipment__centre", "equipment__vendor", "ticket")

    downtime_summary = []
    total_downtime_seconds = 0
    for d in downtimes:
        secs = d.seconds()
        total_downtime_seconds += secs
        hours = round(secs / 3600.0, 1)
        downtime_summary.append({
            "equipment": d.equipment.name,
            "centre": d.equipment.centre.name,
            "hours": hours,
            "ticket": d.ticket.number,
            "active": d.end is None,
        })

    # 3. Open tickets by Centre
    centres = Centre.objects.filter(active=True).order_by("name")
    open_by_centre = []
    for cent in centres:
        c_open = Ticket.objects.filter(centre=cent, status__in=OPEN_STATUSES, is_archived=False)
        count = c_open.count()
        p1_count = c_open.filter(priority=Priority.P1).count()
        if count > 0:
            open_by_centre.append({
                "centre": cent.name,
                "code": cent.code,
                "total": count,
                "p1": p1_count,
            })

    lines = [
        "DiagTrack Weekly Management Summary",
        "Period: " + start_date.strftime("%d %b %Y") + " to " + end_date.strftime("%d %b %Y"),
        "=" * 48,
        "",
        "1. MISSED DEADLINES (SLAs)",
        "-" * 26,
    ]
    if breached_tickets.exists():
        lines.append("Total SLA breaches: " + str(breached_tickets.count()))
        for t in breached_tickets[:15]:
            reasons = []
            if t.response_breached:
                reasons.append("Response missed")
            if t.resolution_breached:
                reasons.append("Resolution missed")
            reason_str = ", ".join(reasons)
            lines.append(f"• {t.number} [{t.priority}] at {t.centre.name}: {t.subject} ({reason_str})")
        if breached_tickets.count() > 15:
            lines.append(f"... and {breached_tickets.count() - 15} more.")
    else:
        lines.append("No SLA deadlines were missed during this period. All tickets on target!")

    lines.extend([
        "",
        "2. EQUIPMENT DOWNTIME",
        "-" * 26,
    ])
    if downtime_summary:
        lines.append("Total downtime logged: " + str(round(total_downtime_seconds / 3600.0, 1)) + " hours across " + str(len(downtime_summary)) + " incident(s).")
        for dt in downtime_summary[:10]:
            status_tag = "STILL DOWN" if dt["active"] else "Resolved"
            lines.append(f"• {dt['equipment']} ({dt['centre']}): {dt['hours']}h [{status_tag}] (Ticket {dt['ticket']})")
        if len(downtime_summary) > 10:
            lines.append(f"... and {len(downtime_summary) - 10} more.")
    else:
        lines.append("No machine downtime was logged this week.")

    lines.extend([
        "",
        "3. CURRENT OPEN BACKLOG BY CENTRE",
        "-" * 26,
    ])
    if open_by_centre:
        for ob in open_by_centre:
            p1_str = f" (including {ob['p1']} P1 Critical)" if ob["p1"] else ""
            lines.append(f"• {ob['centre']}: {ob['total']} open tickets{p1_str}")
    else:
        lines.append("No open tickets across any centre.")

    lines.extend([
        "",
        "=" * 48,
        "DiagTrack Internal Service Desk · Pramodini Medicare Limited"
    ])

    subject = "Weekly Management Summary (" + start_date.strftime("%d %b") + " - " + end_date.strftime("%d %b") + ")"
    return {
        "subject": subject,
        "body": "\n".join(lines),
        "breached_count": breached_tickets.count(),
        "downtime_hours": round(total_downtime_seconds / 3600.0, 1),
        "open_by_centre": open_by_centre,
    }


def send_weekly_management_report(force=False):
    from .models import SiteSettings, User, OutboundMessage
    s = SiteSettings.load()
    today = timezone.localdate()

    if not s.weekly_report_enabled and not force:
        return 0

    if s.last_weekly_report_at == today and not force:
        return 0

    recipients = list(User.objects.filter(
        role__in=[User.MANAGEMENT, User.ADMIN],
        is_active=True
    ).exclude(email=""))

    superusers = list(User.objects.filter(is_superuser=True, is_active=True).exclude(email=""))
    recipients = list({u.pk: u for u in (recipients + superusers)}.values())

    if not recipients:
        return 0

    report = generate_weekly_management_summary()
    sent_count = 0
    for u in recipients:
        OutboundMessage.objects.create(
            channel=OutboundMessage.EMAIL,
            to=u.email,
            subject=report["subject"],
            body=f"Hello {u.display_name},\n\n" + report["body"],
            status=OutboundMessage.PENDING
        )
        sent_count += 1

    s.last_weekly_report_at = today
    s.save(update_fields=["last_weekly_report_at"])
    return sent_count
