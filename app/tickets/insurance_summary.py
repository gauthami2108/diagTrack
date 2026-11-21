"""Monday email: what needs attention in insurance, licences and claims, for each person, limited to what they may see."""
from datetime import timedelta

from django.db.models import Q
from django.utils import timezone

from .models import InsuranceClaim, InsurancePolicy, Licence, OutboundMessage, SiteSettings, User


def build(user, today=None):
    """Sections of lines for one person, or None when nothing needs attention (or no access)."""
    from . import views_insurance as vi, views_licence as vl
    if not user.can_view_insurance:
        return None
    today = today or timezone.localdate()
    cfg = SiteSettings.load()
    sec = []

    def add(title, lines):
        if lines:
            sec.append((title, lines))

    expired, ending, prem = [], [], []
    for p in vi._scoped(user).filter(cancelled=False, renewed=False):
        left = (p.expiry_date - today).days
        st = p.status_on(today, vi._soon(p, cfg))
        label = f"{p.cover_type.name}, {p.location}, {p.insurer} {p.policy_number}"
        if st == InsurancePolicy.EXPIRED:
            expired.append((left, f"{label}: expired {p.expiry_date:%d %b %Y}"))
        elif st == InsurancePolicy.EXPIRING:
            ending.append((left, f"{label}: ends {p.expiry_date:%d %b %Y} ({left} day{'s' if left != 1 else ''})"))
        if p.premium_due and (p.premium_due - today).days <= 14 and p.expiry_date >= today - timedelta(days=30):
            d = (p.premium_due - today).days
            when = "due today" if d == 0 else f"due in {d} day{'s' if d != 1 else ''}" if d > 0 else f"{-d} day{'s' if d != -1 else ''} overdue"
            prem.append((d, f"{label}: premium {when} ({p.premium_due:%d %b %Y})"))
    add("Premiums due or overdue", [t for _, t in sorted(prem)])
    add("Policies expired and not renewed", [t for _, t in sorted(expired, reverse=True)])
    add("Policies ending soon", [t for _, t in sorted(ending)])

    lx, le = [], []
    for x in vl._scoped(user).filter(closed=False, renewed=False):
        st = x.status_on(today)
        left = (x.valid_until - today).days
        if st == Licence.EXPIRED:
            lx.append((left, f"{x.name}, {x.location}: expired {x.valid_until:%d %b %Y}"))
        elif st == Licence.EXPIRING:
            le.append((left, f"{x.name}, {x.location}: ends {x.valid_until:%d %b %Y} ({left} day{'s' if left != 1 else ''})"))
    add("Licences and certificates expired", [t for _, t in sorted(lx, reverse=True)])
    add("Licences and certificates ending soon", [t for _, t in sorted(le)])

    claims = (InsuranceClaim.objects.filter(policy__in=vi._scoped(user), status__in=InsuranceClaim.OPEN)
              .select_related("policy", "policy__cover_type").order_by("incident_date"))
    add("Claims still open", [f"{c.incident_date:%d %b %Y}: {c.description[:80]} ({c.get_status_display()}, {c.policy.cover_type.name})" for c in claims])

    centres = vi.Centre.objects.filter(active=True)
    if not user.insurance_sees_all:
        centres = centres.filter(pk__in=user.centres.all())
    gaps = vi._gap_count(user, centres, cfg)
    if gaps:
        add("Cover missing", [f"{gaps} centre and cover combination{'s' if gaps != 1 else ''} with no current policy. See the Coverage grid."])
    return sec or None


def render(user, sec):
    out = [f"Hello {user.display_name},", "", "Insurance, licences and claims needing attention this week:", ""]
    for title, lines in sec:
        out.append(f"{title} ({len(lines)})")
        out += [f"  - {l}" for l in lines[:15]]
        if len(lines) > 15:
            out.append(f"  ... and {len(lines) - 15} more")
        out.append("")
    out.append("Open Insurance in DiagTrack for the details.")
    return "\n".join(out)


def recipients():
    return (User.objects.filter(is_active=True).exclude(email="")
            .filter(Q(role=User.ADMIN) | Q(is_superuser=True) | Q(insurance_access__in=["own", "all", "manage"]))
            .prefetch_related("centres").distinct())


def send_weekly(force=False, today=None):
    """Queue one email per person with something to act on. Once a day; nothing is sent when nothing needs attention."""
    s = SiteSettings.load()
    today = today or timezone.localdate()
    if not force and (not s.insurance_weekly_enabled or s.last_insurance_summary_at == today):
        return 0
    n = 0
    for u in recipients():
        sec = build(u, today)
        if sec:
            OutboundMessage.objects.create(channel=OutboundMessage.EMAIL, to=u.email,
                                           subject="Weekly insurance summary", body=render(u, sec),
                                           status=OutboundMessage.PENDING)
            n += 1
    s.last_insurance_summary_at = today
    s.save(update_fields=["last_insurance_summary_at"])
    return n
