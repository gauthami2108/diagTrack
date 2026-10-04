"""Numbers for the home page and the dashboard. All counts respect what the person may see."""
from datetime import timedelta

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
