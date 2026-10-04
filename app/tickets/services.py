"""
The ticket engine: routing, assignment, status changes, SLA clock, escalation.

Screens call these functions; nothing else changes a ticket. Every change is
written to TicketHistory (the audit trail) and raises the right alerts.
"""
from datetime import timedelta

from django.db import transaction
from django.db.models import Count, Q
from django.utils import timezone

from . import notify as nt
from .models import (
    OPEN_STATUSES, RUNNING_STATUSES, Centre, Department, DowntimeLog, EscalationLog,
    EscalationRule, Heartbeat, Priority, SiteSettings, SlaPolicy, Status, Ticket,
    TicketAttachment, TicketComment, TicketHistory, User,
)

DEFAULT_SLA = {"P1": (15, 240), "P2": (30, 480), "P3": (120, 1440), "P4": (240, 4320)}


class NotAllowed(Exception):
    """Raised when a person tries something their role or the ticket status does not allow."""


# --------------------------------------------------------------------------
# Who can see and do what
# --------------------------------------------------------------------------
def visible_tickets(user):
    qs = Ticket.objects.select_related("centre", "category", "department", "requester", "assignee", "equipment")
    if user.sees_everything:
        return qs
    q = Q(requester=user) | Q(assignee=user) | Q(department__head=user)
    dept_ids = list(user.departments.values_list("id", flat=True))
    centre_ids = list(user.centres.values_list("id", flat=True))
    if user.role == User.DEPT_HEAD:
        q |= Q(department_id__in=dept_ids)
    elif user.role == User.AGENT:
        q |= Q(department_id__in=dept_ids, centre_id__in=centre_ids) if centre_ids else Q(department_id__in=dept_ids)
    elif user.role in (User.CENTRE_MANAGER, User.AREA_MANAGER):
        q |= Q(centre_id__in=centre_ids)
    return qs.filter(q).distinct()


def _in_department(user, ticket):
    return user.role in (User.AGENT, User.DEPT_HEAD) and user.departments.filter(pk=ticket.department_id).exists()


def _is_head(user, ticket):
    return user.is_admin or ticket.department.head_id == user.pk or (
        user.role == User.DEPT_HEAD and user.departments.filter(pk=ticket.department_id).exists())


def _covers_centre(user, ticket):
    return user.role in (User.CENTRE_MANAGER, User.AREA_MANAGER) and user.centres.filter(pk=ticket.centre_id).exists()


def can_reopen(ticket, settings=None):
    s = settings or SiteSettings.load()
    return (ticket.status in (Status.RESOLVED, Status.CLOSED) and ticket.resolved_at
            and timezone.now() <= ticket.resolved_at + timedelta(days=s.reopen_days))


def allowed_actions(user, t):
    """The set of buttons this person gets on this ticket."""
    if user.role == User.MANAGEMENT and not user.is_superuser:
        return set()  # view only
    head = _is_head(user, t)
    member = _in_department(user, t)
    owner = t.assignee_id == user.pk
    requester = t.requester_id == user.pk
    worker = owner or head
    a = set()
    if t.is_open:
        a.add("comment")
        if t.status in (Status.NEW, Status.ASSIGNED) and (owner or head or (member and t.assignee_id is None)):
            a.add("start")
        if t.status == Status.IN_PROGRESS and worker:
            a.update({"hold", "resolve"})
        if t.status == Status.ON_HOLD and worker:
            a.add("resume")
        if head:
            a.update({"assign", "set_priority", "cancel"})
        if worker or member or head or (user.role == User.AREA_MANAGER and _covers_centre(user, t)):
            a.add("transfer")
        if requester and t.status in (Status.NEW, Status.ASSIGNED):
            a.add("cancel")
        if requester and not t.priority_raised and t.priority != Priority.P1 and not head:
            a.add("raise_priority")
    else:
        if t.status != Status.CANCELLED:
            a.add("comment")
        if t.status == Status.RESOLVED and (requester or user.is_admin):
            a.add("close")
        if (requester or user.is_admin) and can_reopen(t):
            a.add("reopen")
    if member or head or user.sees_everything or _covers_centre(user, t):
        a.add("internal_note")
    return a


def _require(user, ticket, action):
    if action not in allowed_actions(user, ticket):
        raise NotAllowed("You cannot do that on this ticket right now.")


# --------------------------------------------------------------------------
# SLA clock
# --------------------------------------------------------------------------
def sla_minutes(department, priority):
    policy = (SlaPolicy.objects.filter(department=department, priority=priority).first()
              or SlaPolicy.objects.filter(department__isnull=True, priority=priority).first())
    if policy:
        return policy.response_minutes, policy.resolution_minutes
    return DEFAULT_SLA[priority]


def _set_deadlines(t):
    paused = timedelta(seconds=t.hold_seconds)
    t.response_due = t.sla_started_at + timedelta(minutes=t.response_minutes) + paused
    t.resolution_due = t.sla_started_at + timedelta(minutes=t.resolution_minutes) + paused


def start_sla(t, now=None):
    """Start (or restart, after a transfer) the clock for the ticket's department and priority."""
    t.sla_started_at = now or timezone.now()
    t.response_minutes, t.resolution_minutes = sla_minutes(t.department, t.priority)
    t.hold_seconds = 0
    t.hold_started_at = None
    t.first_response_at = None
    t.response_breached = t.resolution_breached = False
    _set_deadlines(t)


def _mark_response(t, user, now=None):
    if t.first_response_at is None and user.pk != t.requester_id:
        t.first_response_at = now or timezone.now()


def log(t, actor, text, field="", old="", new="", note=""):
    TicketHistory.objects.create(ticket=t, actor=actor, text=text[:300], field=field,
                                 old=str(old)[:200], new=str(new)[:200], note=note)


# --------------------------------------------------------------------------
# Routing and assignment
# --------------------------------------------------------------------------
def route_department(category, equipment):
    """Rule 1: category decides. Rule 2: equipment tickets go to the equipment department."""
    if equipment is not None:
        dept = Department.objects.filter(handles_equipment=True, active=True).first()
        if dept:
            return dept
    return category.department


def candidate_agents(department, centre):
    """Agents of the department who cover this centre (no centres ticked = covers all)."""
    agents = User.objects.filter(role=User.AGENT, is_active=True, departments=department)
    return [u for u in agents.order_by("id") if not u.centres.exists() or u.centres.filter(pk=centre.pk).exists()]


def pick_assignee(department, centre):
    if department.assignment_mode == Department.MANUAL:
        return None
    agents = candidate_agents(department, centre)
    if not agents:
        return None
    if department.assignment_mode == Department.LEAST_LOADED:
        load = dict(Ticket.objects.filter(assignee__in=agents, status__in=OPEN_STATUSES)
                    .order_by().values_list("assignee").annotate(n=Count("id")))
        return min(agents, key=lambda u: (load.get(u.pk, 0), u.pk))
    # round robin: the agent after the one who got the last ticket
    last = department.last_assigned_id
    later = [u for u in agents if last is not None and u.pk > last]
    chosen = later[0] if later else agents[0]
    Department.objects.filter(pk=department.pk).update(last_assigned=chosen)
    department.last_assigned_id = chosen.pk
    return chosen


def _next_number(centre):
    with transaction.atomic():
        c = Centre.objects.select_for_update().get(pk=centre.pk)
        seq = c.next_seq
        c.next_seq = seq + 1
        c.save(update_fields=["next_seq"])
    return f"{c.code}-{seq:06d}"


def save_attachments(ticket, files, user, comment=None):
    limit = SiteSettings.load().max_attachment_mb * 1024 * 1024
    skipped = []
    for f in files:
        if f.size > limit:
            skipped.append(f.name)
            continue
        TicketAttachment.objects.create(ticket=ticket, comment=comment, file=f, name=f.name[:200],
                                        size=f.size, uploaded_by=user)
    return skipped


@transaction.atomic
def create_ticket(user, centre, category, subject, description, equipment=None,
                  patient_impact=False, equipment_down=False, extra=None, files=(), now=None):
    now = now or timezone.now()
    department = route_department(category, equipment)
    priority = Priority.P1 if patient_impact else category.default_priority
    t = Ticket(number=_next_number(centre), centre=centre, category=category, department=department,
               requester=user, priority=priority, subject=subject.strip()[:160], description=description.strip(),
               equipment=equipment, patient_impact=patient_impact, extra=extra or {}, created_at=now)
    start_sla(t, now)
    assignee = pick_assignee(department, centre)
    if assignee:
        t.assignee, t.status = assignee, Status.ASSIGNED
    t.save()
    log(t, user, "Raised the ticket")
    if assignee:
        log(t, None, f"Assigned to {assignee.display_name} automatically", "assignee", "", assignee.display_name)
    if equipment is not None and equipment_down:
        DowntimeLog.objects.create(equipment=equipment, ticket=t, start=now)
        log(t, user, f"Marked {equipment.name} as down")
    skipped = save_attachments(t, files, user)
    _alert_new(t)
    t.skipped_files = skipped
    return t


def _alert_new(t):
    s = SiteSettings.load()
    urgent = t.priority == Priority.P1
    what = f"{t.number} {t.priority}: {t.subject}"
    where = f"{t.centre.name}, {t.category.name}. Raised by {t.requester.display_name}."
    if t.assignee:
        nt.notify([t.assignee], t, f"New ticket assigned to you: {what}", where, urgent)
    else:
        nt.notify([t.department.head], t, f"New ticket needs assigning: {what}", where, urgent)
    watchers = []
    if s.area_manager_all or urgent:
        watchers += nt.users_with_role(User.AREA_MANAGER, t.centre)
    if urgent:
        watchers += [t.department.head] + nt.users_with_role(User.CENTRE_MANAGER, t.centre)
    skip = {t.assignee_id or t.department.head_id}
    nt.notify([u for u in watchers if u and u.pk not in skip], t,
              f"{'Critical ticket' if urgent else 'New ticket'} at {t.centre.name}: {what}", where, urgent,
              exclude=t.requester)


# --------------------------------------------------------------------------
# Actions
# --------------------------------------------------------------------------
@transaction.atomic
def assign(t, actor, assignee, check=True):
    if check:
        _require(actor, t, "assign")
    old = t.assignee.display_name if t.assignee else "Unassigned"
    t.assignee = assignee
    if t.status == Status.NEW:
        t.status = Status.ASSIGNED
    t.save()
    log(t, actor, f"Assigned to {assignee.display_name}", "assignee", old, assignee.display_name)
    nt.notify([assignee], t, f"Ticket assigned to you: {t.number} {t.priority}: {t.subject}",
              f"{t.centre.name}, {t.category.name}.", t.priority == Priority.P1, exclude=actor)


@transaction.atomic
def start_work(t, actor):
    _require(actor, t, "start")
    old = t.get_status_display()
    if t.assignee_id != actor.pk:  # unassigned, or the head takes it over
        was = t.assignee.display_name if t.assignee else "Unassigned"
        t.assignee = actor
        log(t, actor, "Picked up the ticket", "assignee", was, actor.display_name)
    t.status = Status.IN_PROGRESS
    _mark_response(t, actor)
    t.save()
    log(t, actor, "Started work", "status", old, t.get_status_display())
    nt.notify([t.requester], t, f"Work started on {t.number}",
              f"{(t.assignee or actor).display_name} is working on: {t.subject}", exclude=actor)


@transaction.atomic
def hold(t, actor, reason, note=""):
    _require(actor, t, "hold")
    labels = dict(Ticket._meta.get_field("hold_reason").choices)
    if reason not in labels:
        raise NotAllowed("Choose a reason for putting the ticket on hold.")
    t.status, t.hold_reason, t.hold_started_at = Status.ON_HOLD, reason, timezone.now()
    t.save()
    log(t, actor, f"Put on hold: {labels[reason]}", "status", "In progress", "On hold", note)
    nt.notify([t.requester], t, f"{t.number} is on hold: {labels[reason]}", note, exclude=actor)


@transaction.atomic
def resume(t, actor):
    _require(actor, t, "resume")
    _resume_clock(t)
    t.status, t.hold_reason = Status.IN_PROGRESS, ""
    t.save()
    log(t, actor, "Resumed work", "status", "On hold", "In progress")


def _resume_clock(t, now=None):
    if t.hold_started_at:
        now = now or timezone.now()
        t.hold_seconds += int((now - t.hold_started_at).total_seconds())
        t.hold_started_at = None
        _set_deadlines(t)


@transaction.atomic
def resolve(t, actor, note, downtime_end=None):
    _require(actor, t, "resolve")
    if not note.strip():
        raise NotAllowed("Write a short resolution note so the requester knows what was done.")
    now = timezone.now()
    downtime = getattr(t, "downtime", None)
    if downtime and not downtime.end:
        end = downtime_end or now
        if end < downtime.start or end > now + timedelta(minutes=1):
            raise NotAllowed("The time the equipment came back must be after it went down and not in the future.")
        downtime.end = end
        downtime.save()
    t.status, t.resolved_at, t.resolution_note = Status.RESOLVED, now, note.strip()
    t.resolution_breached = t.resolution_breached or now > t.resolution_due
    _mark_response(t, actor, now)
    t.save()
    log(t, actor, "Resolved the ticket", "status", "In progress", "Resolved", note.strip())
    s = SiteSettings.load()
    nt.notify([t.requester], t, f"{t.number} is resolved. Please confirm.",
              f"{note.strip()[:200]} It closes by itself in {s.auto_close_days} days.", exclude=actor)


@transaction.atomic
def close(t, actor, rating=None, auto=False):
    if not auto:
        _require(actor, t, "close")
    t.status, t.closed_at = Status.CLOSED, timezone.now()
    if rating:
        t.rating = max(1, min(5, int(rating)))
    t.save()
    log(t, actor, "Closed automatically, no reply from requester" if auto else "Confirmed the fix and closed the ticket",
        "status", "Resolved", "Closed")
    if not auto:
        nt.notify([t.assignee], t, f"{t.number} was confirmed and closed",
                  f"Rating: {t.rating} of 5" if t.rating else "", exclude=actor)


@transaction.atomic
def reopen(t, actor, reason):
    _require(actor, t, "reopen")
    if not reason.strip():
        raise NotAllowed("Say what is still wrong so the agent can pick it up.")
    old = t.get_status_display()
    now = timezone.now()
    # the clock continues from where it stopped: time spent resolved does not count
    t.hold_seconds += int((now - t.resolved_at).total_seconds())
    _set_deadlines(t)
    t.status = Status.IN_PROGRESS if t.assignee_id else Status.NEW
    t.resolved_at = t.closed_at = None
    t.reopen_count += 1
    t.save()
    log(t, actor, "Reopened the ticket", "status", old, t.get_status_display(), reason.strip())
    nt.notify([t.assignee, t.department.head], t, f"{t.number} was reopened", reason.strip()[:300],
              t.priority == Priority.P1, exclude=actor)


@transaction.atomic
def cancel(t, actor, reason):
    _require(actor, t, "cancel")
    if not reason.strip():
        raise NotAllowed("Give a reason for cancelling.")
    old = t.get_status_display()
    t.status, t.closed_at = Status.CANCELLED, timezone.now()
    t.save()
    DowntimeLog.objects.filter(ticket=t, end__isnull=True).update(end=timezone.now())
    log(t, actor, "Cancelled the ticket", "status", old, "Cancelled", reason.strip())
    nt.notify([t.assignee, t.requester], t, f"{t.number} was cancelled", reason.strip()[:300], exclude=actor)


@transaction.atomic
def transfer(t, actor, department, reason):
    _require(actor, t, "transfer")
    if not reason.strip():
        raise NotAllowed("Give a reason for the transfer.")
    if department.pk == t.department_id:
        raise NotAllowed("The ticket is already with that department.")
    old = t.department.name
    t.department = department
    start_sla(t)  # rule 6: the new department gets a fresh clock
    t.hold_reason = ""
    EscalationLog.objects.filter(ticket=t).delete()
    assignee = pick_assignee(department, t.centre)
    t.assignee = assignee
    t.status = Status.ASSIGNED if assignee else Status.NEW
    t.save()
    log(t, actor, f"Transferred from {old} to {department.name}", "department", old, department.name, reason.strip())
    target = [assignee] if assignee else [department.head]
    nt.notify(target, t, f"Ticket transferred to {department.name}: {t.number} {t.priority}: {t.subject}",
              reason.strip()[:300], t.priority == Priority.P1, exclude=actor)
    nt.notify([t.requester], t, f"{t.number} was moved to {department.name}", reason.strip()[:300], exclude=actor)


@transaction.atomic
def change_priority(t, actor, priority, reason):
    if priority not in Priority.values or priority == t.priority:
        raise NotAllowed("Choose a different priority.")
    by_requester = "set_priority" not in allowed_actions(actor, t)
    if by_requester:
        _require(actor, t, "raise_priority")
        if priority >= t.priority:  # "P1" < "P2": smaller is more urgent
            raise NotAllowed("You can only raise the priority. Ask the department head to lower it.")
    if not reason.strip():
        raise NotAllowed("Give a reason for changing the priority.")
    old = t.priority
    t.priority = priority
    t.response_minutes, t.resolution_minutes = sla_minutes(t.department, priority)
    _set_deadlines(t)
    if by_requester:
        t.priority_raised = True
    t.save()
    EscalationLog.objects.filter(ticket=t).delete()
    log(t, actor, f"Changed priority from {old} to {priority}", "priority", old, priority, reason.strip())
    nt.notify([t.assignee, t.department.head], t, f"{t.number} priority is now {priority}", reason.strip()[:300],
              priority == Priority.P1, exclude=actor)


@transaction.atomic
def add_comment(t, actor, body, internal=False, files=()):
    _require(actor, t, "comment")
    if internal and "internal_note" not in allowed_actions(actor, t):
        internal = False
    if not body.strip() and not files:
        raise NotAllowed("Write something or attach a file.")
    c = TicketComment.objects.create(ticket=t, author=actor, body=body.strip(), internal=internal)
    skipped = save_attachments(t, files, actor, c)
    if not internal and (t.assignee_id == actor.pk or _in_department(actor, t)) and t.first_response_at is None:
        _mark_response(t, actor)
    t.save()  # bumps updated_at
    who = [t.assignee, t.department.head if not t.assignee else None]
    if not internal:
        who.append(t.requester)
    kind = "Internal note" if internal else "New reply"
    nt.notify(who, t, f"{kind} on {t.number} from {actor.display_name}", body.strip()[:300], exclude=actor)
    return c, skipped


# --------------------------------------------------------------------------
# Background checks (run by the worker every minute)
# --------------------------------------------------------------------------
def _escalation_targets(rule, t):
    people = []
    if rule.notify_assignee:
        people.append(t.assignee)
    if rule.notify_dept_head:
        people.append(t.department.head)
    if rule.notify_centre_manager:
        people += nt.users_with_role(User.CENTRE_MANAGER, t.centre)
    if rule.notify_area_manager:
        people += nt.users_with_role(User.AREA_MANAGER, t.centre)
    return people


def run_sla_checks(now=None):
    """Mark breaches, hand unaccepted tickets back to the head, and fire escalation rules."""
    now = now or timezone.now()
    rules = list(EscalationRule.objects.filter(active=True))
    fired = 0
    tickets = (Ticket.objects.filter(status__in=RUNNING_STATUSES)
               .select_related("department__head", "assignee", "centre", "category", "requester"))
    for t in tickets:
        changed = False
        if t.first_response_at is None and not t.response_breached and now > t.response_due:
            t.response_breached = changed = True
            log(t, None, "Response time missed")
            head = t.department.head
            if t.status in (Status.NEW, Status.ASSIGNED) and head and t.assignee_id != head.pk:
                old = t.assignee.display_name if t.assignee else "Unassigned"
                t.assignee, t.status = head, Status.ASSIGNED
                log(t, None, f"Not accepted in time. Returned to {head.display_name}", "assignee", old,
                    head.display_name)
        if not t.resolution_breached and now > t.resolution_due:
            t.resolution_breached = changed = True
            log(t, None, "Resolution time missed")
        if changed:
            t.save()
        done = set(EscalationLog.objects.filter(ticket=t).values_list("rule_id", flat=True))
        for rule in rules:
            if rule.pk in done or not rule.applies_to(t.priority):
                continue
            if rule.basis == EscalationRule.RESPONSE and t.first_response_at is not None:
                continue
            if t.percent_used(rule.basis, now) < rule.percent:
                continue
            EscalationLog.objects.create(ticket=t, rule=rule)
            due = t.response_due if rule.basis == EscalationRule.RESPONSE else t.resolution_due
            nt.notify(_escalation_targets(rule, t), t, f"{rule.name}: {t.number} {t.priority}: {t.subject}",
                      f"{t.centre.name}, {t.department.name}. Due {timezone.localtime(due):%d %b %H:%M}. "
                      f"Owner: {t.assignee.display_name if t.assignee else 'unassigned'}.", urgent=True)
            log(t, None, f"Escalated: {rule.name}")
            fired += 1
    return fired


def auto_close(now=None):
    now = now or timezone.now()
    s = SiteSettings.load()
    cutoff = now - timedelta(days=s.auto_close_days)
    n = 0
    for t in Ticket.objects.filter(status=Status.RESOLVED, resolved_at__lte=cutoff):
        close(t, None, auto=True)
        n += 1
    return n


def beat(name, detail=""):
    Heartbeat.objects.update_or_create(name=name, defaults={"at": timezone.now(), "detail": detail[:300]})
