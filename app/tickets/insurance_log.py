"""The change history of insurance policies: what a policy looked like before, what changed, and who did it."""
from .models import InsuranceLog, LicenceLog

MONEY = ("Sum insured", "Premium")


def _d(v):
    return v.strftime("%d %b %Y") if v else "none"


def snapshot(p):
    """The things worth recording about a policy, as plain text by label."""
    return {
        "Type of cover": p.cover_type.name,
        "Insurer": p.insurer,
        "Policy number": p.policy_number,
        "Broker or agent": p.contact or "none",
        "Covers": p.covers or "none",
        "Start date": _d(p.start_date),
        "Expiry date": _d(p.expiry_date),
        "Next premium due": _d(p.premium_due),
        "First reminder (days)": str(p.remind_days) if p.remind_days else "Settings",
        "Cancelled": "Yes" if p.cancelled else "No",
        "Notes": p.notes or "",
        "Sum insured": "" if p.sum_insured is None else str(p.sum_insured),
        "Premium": "" if p.premium is None else str(p.premium),
        "Centres": sorted(c.name for c in p.centres.all()) or ["all centres"],
        "Responsible": sorted(u.display_name for u in p.responsible.all()),
        "Machines": sorted(e.name for e in p.equipment.all()),
    }


def changes(before, after):
    """A short sentence for each thing that changed. Money and notes are recorded as changed, never with their values."""
    out = []
    for label, old in before.items():
        new = after[label]
        if old == new:
            continue
        if label in MONEY or label == "Notes":
            out.append(f"{label} changed")
        elif isinstance(old, list):
            added, removed = sorted(set(new) - set(old)), sorted(set(old) - set(new))
            bits = ([f"added {', '.join(added)}"] if added else []) + ([f"removed {', '.join(removed)}"] if removed else [])
            out.append(f"{label}: " + "; ".join(bits))
        else:
            out.append(f"{label}: {old} → {new}")
    return out


def record(policy, actor, text):
    return InsuranceLog.objects.create(policy=policy, actor=actor, text=text[:500])


def record_changes(policy, actor, before, prefix=""):
    lines = changes(before, snapshot(policy))
    for line in lines:
        record(policy, actor, prefix + line)
    return lines


# --- licences and certificates: the same idea ---------------------------------
def licence_snapshot(x):
    return {
        "Name": x.name,
        "Issued by": x.issuer or "none",
        "Number": x.reference_number or "none",
        "Issued on": _d(x.issued_on),
        "Valid until": _d(x.valid_until),
        "First reminder (days)": str(x.remind_days) if x.remind_days else "90",
        "No longer needed": "Yes" if x.closed else "No",
        "Notes": x.notes or "",
        "Centres": sorted(c.name for c in x.centres.all()) or ["all centres"],
        "Responsible": sorted(u.display_name for u in x.responsible.all()),
        "Machine": x.equipment.name if x.equipment_id else "none",
    }


def licence_record(x, actor, text):
    return LicenceLog.objects.create(licence=x, actor=actor, text=text[:500])


def licence_record_changes(x, actor, before, prefix=""):
    lines = changes(before, licence_snapshot(x))
    for line in lines:
        licence_record(x, actor, prefix + line)
    return lines
