"""
Wording of email, SMS and WhatsApp alerts.

Each alert has an event (raised, resolved, escalation and so on). For every event and channel the
administrator can change the wording on the Message templates screen; until then the built-in wording
below is used. Placeholders in curly brackets, such as {ticket}, are filled in when the alert is sent.
A line whose placeholders all come out empty (for example "Machine: {equipment}" on a ticket with no
machine) is left out, so one template works for every ticket.
"""
import re

from django.utils import timezone

EMAIL, SMS, WHATSAPP = "email", "sms", "whatsapp"
CHANNELS = [(EMAIL, "Email"), (SMS, "SMS"), (WHATSAPP, "WhatsApp")]
LIMITS = {SMS: 480, WHATSAPP: 1000}

# (key, label, group, what it is)
EVENTS = [
    ("raised", "New ticket", "Tickets", "A ticket is raised. Goes to the owner or department head, and to managers"),
    ("assigned", "Assigned", "Tickets", "A ticket is assigned or reassigned. Goes to the new owner"),
    ("started", "Work started", "Tickets", "The agent starts work. Goes to the requester"),
    ("hold", "On hold", "Tickets", "The ticket is put on hold. Goes to the requester"),
    ("resolved", "Resolved", "Tickets", "The team resolves it. Goes to the requester, asking them to confirm"),
    ("closed", "Closed", "Tickets", "The ticket is closed. Goes to the owner, head and managers"),
    ("reopened", "Reopened", "Tickets", "The requester reopens it. Goes to the owner and head"),
    ("cancelled", "Cancelled", "Tickets", "The ticket is cancelled. Goes to the owner and requester"),
    ("transferred", "Transferred", "Tickets", "Moved to another department. Goes to that department and the requester"),
    ("priority", "Priority changed", "Tickets", "The priority changes. Goes to the owner and head"),
    ("reply", "New reply", "Conversation", "A public comment. Goes to the requester and owner"),
    ("note", "Internal note", "Conversation", "An internal note. Goes to the owner or head"),
    ("mention", "Mentioned", "Conversation", "Someone is @mentioned in a comment"),
    ("escalation", "Escalation", "Deadlines and equipment", "An escalation rule fires. Goes to the people the rule names"),
    ("amc", "AMC ending", "Deadlines and equipment", "A machine's AMC is about to end. Goes to the equipment head and administrators"),
    ("backupfail", "Backup failed", "Backups", "A backup, its weekly check or the monthly test restore failed. Goes to administrators and the people chosen on the Backups page"),
    ("backuplate", "Backup overdue", "Backups", "No good backup for longer than the destination allows. Goes to the same people"),
    ("signinlock", "Sign-in paused", "Security", "Too many wrong passwords for a user name or from one computer. Goes to administrators"),
    ("other", "Anything else", "Deadlines and equipment", "Any other alert"),
]
EVENT_KEYS = [e[0] for e in EVENTS]
EVENT_LABELS = {e[0]: e[1] for e in EVENTS}

PLACEHOLDERS = [
    ("title", "The alert's headline, as shown on the bell"),
    ("details", "The alert's detail: the comment, the reason or the resolution note"),
    ("recipient", "The name of the person receiving it"),
    ("ticket", "Ticket number, for example KKP-000123"),
    ("subject", "Ticket title"),
    ("priority", "Priority, for example P1 Critical"),
    ("status", "Current status"),
    ("centre", "Centre"),
    ("department", "Department"),
    ("category", "Category"),
    ("equipment", "Machine, if any"),
    ("requester", "Who raised it"),
    ("owner", "Who it is assigned to"),
    ("due", "Resolution deadline"),
    ("link", "Link to the ticket, to the machine for AMC alerts, or to the Backups page"),
    ("org", "Organisation name"),
]
KEYS = {k for k, _ in PLACEHOLDERS}

_TICKET_EMAIL = """Hello {recipient},

{title}
{details}

Ticket: {ticket}, {priority}
Title: {subject}
Centre: {centre}
Department: {department}
Machine: {equipment}
Owner: {owner}
Status: {status}
Due: {due}

Open the ticket: {link}

{org} service desk"""

_AMC_EMAIL = """Hello {recipient},

{title}
{details}

Open the machine: {link}

{org} service desk"""

_PLAIN_EMAIL = """Hello {recipient},

{title}
{details}

Open DiagTrack: {link}

{org} service desk"""

_SMS = "{org}: {title}. {link}"
_WHATSAPP = """*{title}*
{details}

Centre: {centre}
Owner: {owner}
Due: {due}
{link}"""
_AMC_WHATSAPP = """*{title}*
{details}
{link}"""


def default(event, channel):
    """Built-in wording: (send, subject, body)."""
    if channel == EMAIL:
        body = _AMC_EMAIL if event == "amc" else _PLAIN_EMAIL if event in ("backupfail", "backuplate", "signinlock") else _TICKET_EMAIL
        return ("always", "[{org}] {title}", body)
    send = "always" if event in ("escalation", "backupfail", "backuplate") else "urgent"
    if event == "signinlock" and channel != EMAIL:
        send = "never"  # by email only: a burst of guessing should not become a burst of SMS
    if channel == SMS:
        return (send, "", _SMS)
    return (send, "", _AMC_WHATSAPP if event in ("amc", "backupfail", "backuplate", "signinlock") else _WHATSAPP)


def get(event, channel):
    """The template in use: the saved one, or the built-in wording. Returns a dict."""
    from .models import MessageTemplate
    event = event if event in EVENT_KEYS else "other"
    row = MessageTemplate.objects.filter(event=event, channel=channel).first()
    if row:
        return {"send": row.send, "subject": row.subject, "body": row.body,
                "provider_template": row.provider_template, "custom": True}
    send, subject, body = default(event, channel)
    return {"send": send, "subject": subject, "body": body, "provider_template": "", "custom": False}


PLACE = re.compile(r"\{(\w+)\}")


def render(text, ctx):
    """Fill in the placeholders. Unknown ones are left as typed, so a mistake is visible, not silent."""
    out = []
    for line in (text or "").splitlines():
        keys = [k for k in PLACE.findall(line) if k in KEYS]
        if keys and all(not str(ctx.get(k) or "").strip() for k in keys):
            continue  # every field on this line is empty: leave the line out
        out.append(PLACE.sub(lambda m: str(ctx.get(m.group(1)) or "") if m.group(1) in KEYS else m.group(0), line))
    text = "\n".join(out)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def context(ticket, title, body, settings, recipient=None, link=""):
    ctx = {"title": title, "details": body, "org": settings.org_name,
           "recipient": recipient.display_name if recipient else ""}
    base = settings.site_url.rstrip("/")
    if ticket is not None:
        ctx.update({
            "ticket": ticket.number, "subject": ticket.subject, "priority": ticket.get_priority_display(),
            "status": ticket.get_status_display(), "centre": ticket.centre.name,
            "department": ticket.department.name, "category": ticket.category.name,
            "equipment": ticket.equipment.name if ticket.equipment_id else "",
            "requester": ticket.requester.display_name,
            "owner": ticket.assignee.display_name if ticket.assignee_id else "Unassigned",
            "due": f"{timezone.localtime(ticket.resolution_due):%d %b, %H:%M}" if ticket.resolution_due else "",
            "link": f"{base}/tickets/{ticket.number}/" if base else "",
        })
    elif link and base:
        ctx["link"] = base + link
    return ctx


SAMPLE = {
    "title": "New ticket assigned to you: KKP-000123 P2: CT scanner showing error E-204",
    "details": "Kukatpally, CT and MRI. Raised by Lakshmi R.",
    "recipient": "Ravi Kumar", "ticket": "KKP-000123", "subject": "CT scanner showing error E-204",
    "priority": "P2 High", "status": "Assigned", "centre": "Kukatpally", "department": "Biomedical",
    "category": "CT and MRI", "equipment": "CT scanner 128 slice", "requester": "Lakshmi R",
    "owner": "Ravi Kumar", "due": "10 Oct, 18:30", "link": "http://diagtrack.local/tickets/KKP-000123/",
}


def sample(settings):
    return dict(SAMPLE, org=settings.org_name, link=(settings.site_url.rstrip("/") or "http://diagtrack.local") +
                "/tickets/KKP-000123/")


def should_send(send, urgent):
    return send == "always" or (send == "urgent" and urgent)
