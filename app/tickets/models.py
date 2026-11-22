from django.contrib.auth.models import AbstractUser
from django.core.validators import RegexValidator
from django.db import models
from django.utils import timezone

from .crypto import EncryptedTextField


# --------------------------------------------------------------------------
# Masters
# --------------------------------------------------------------------------
class Centre(models.Model):
    code = models.CharField(
        "short code", max_length=5, unique=True,
        validators=[RegexValidator(r"^[A-Z0-9]{2,5}$", "Use 2 to 5 capital letters or digits.")],
        help_text="Used as the ticket number prefix, for example KKP gives KKP-000123.",
    )
    name = models.CharField(max_length=120)
    city = models.CharField(max_length=80, blank=True)
    active = models.BooleanField(default=True, help_text="Untick to hide a closed centre. Its old tickets stay.")
    next_seq = models.PositiveIntegerField(default=1, editable=False)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


class Department(models.Model):
    ROUND_ROBIN, LEAST_LOADED, MANUAL = "round_robin", "least_loaded", "manual"
    MODES = [
        (ROUND_ROBIN, "Round robin: each agent in turn"),
        (LEAST_LOADED, "Least loaded: agent with fewest open tickets"),
        (MANUAL, "Manual: the department head assigns"),
    ]
    name = models.CharField(max_length=80, unique=True)
    head = models.ForeignKey(
        "User", null=True, blank=True, on_delete=models.SET_NULL, related_name="heads",
        help_text="Receives unassigned tickets and escalations for this department.",
    )
    assignment_mode = models.CharField("how tickets are assigned", max_length=20, choices=MODES, default=ROUND_ROBIN)
    handles_equipment = models.BooleanField(
        "receives equipment tickets", default=False,
        help_text="Tick for one department only (Biomedical). Any ticket with equipment selected goes here.",
    )
    active = models.BooleanField(default=True)
    last_assigned = models.ForeignKey("User", null=True, blank=True, editable=False,
                                      on_delete=models.SET_NULL, related_name="+")

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


class Priority(models.TextChoices):
    P1 = "P1", "P1 Critical"
    P2 = "P2", "P2 High"
    P3 = "P3", "P3 Medium"
    P4 = "P4", "P4 Low"


class Category(models.Model):
    name = models.CharField(max_length=120)
    department = models.ForeignKey(Department, on_delete=models.PROTECT, related_name="categories",
                                   help_text="Tickets in this category are sent to this department.")
    default_priority = models.CharField(max_length=2, choices=Priority.choices, default=Priority.P3)
    asks_equipment = models.BooleanField(
        "ask which equipment", default=False,
        help_text="Tick to show the equipment list when this category is chosen.",
    )
    asks_description = models.BooleanField(
        "ask 'What happened?'", default=True,
        help_text="Untick to hide the free-text 'What happened?' box and use only this category's own questions.",
    )
    active = models.BooleanField(default=True)

    class Meta:
        ordering = ["department__name", "name"]
        verbose_name_plural = "categories"
        unique_together = [("department", "name")]

    def __str__(self):
        return self.name


class CategoryField(models.Model):
    TEXT, LONGTEXT, NUMBER, CHOICE = "text", "longtext", "number", "choice"
    KINDS = [(TEXT, "Text"), (LONGTEXT, "Long text (big box)"), (NUMBER, "Number"), (CHOICE, "Pick from a list")]
    category = models.ForeignKey(Category, on_delete=models.CASCADE, related_name="fields")
    label = models.CharField(max_length=60, help_text="For example: Error code, Room number.")
    kind = models.CharField(max_length=10, choices=KINDS, default=TEXT)
    choices = models.CharField(max_length=300, blank=True,
                               help_text="Only for 'Pick from a list'. Separate options with commas.")
    required = models.BooleanField(default=False)
    order = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ["order", "id"]
        verbose_name = "extra question"

    def __str__(self):
        return self.label

    def choice_list(self):
        return [c.strip() for c in self.choices.split(",") if c.strip()]


class User(AbstractUser):
    REQUESTER, AGENT, DEPT_HEAD = "requester", "agent", "dept_head"
    CENTRE_MANAGER, AREA_MANAGER, MANAGEMENT, ADMIN = "centre_manager", "area_manager", "management", "admin"
    ROLES = [
        (REQUESTER, "Requester: raises tickets"),
        (AGENT, "Agent: resolves tickets"),
        (DEPT_HEAD, "Department head"),
        (CENTRE_MANAGER, "Centre manager"),
        (AREA_MANAGER, "Area manager"),
        (MANAGEMENT, "Management: view only"),
        (ADMIN, "Administrator"),
    ]
    role = models.CharField(max_length=20, choices=ROLES, default=REQUESTER)
    INSURANCE_ACCESS = [
        ("", "No access"),
        ("own", "View: policies of own centres"),
        ("all", "View: all centres"),
        ("manage", "Manage: view all, add, edit and renew"),
    ]
    insurance_access = models.CharField(
        max_length=10, blank=True, default="", choices=INSURANCE_ACCESS,
        help_text="Who may see the Insurance register. Administrators always have full access.")
    mobile = models.CharField(max_length=15, blank=True,
                              help_text="With country code, for SMS and WhatsApp alerts. Example: 919876543210")
    telegram_chat_id = models.CharField("Telegram chat ID", max_length=60, blank=True,
                                        help_text="Chat ID for Telegram alerts (send /start to the bot to find it).")
    centres = models.ManyToManyField(
        Centre, blank=True, related_name="users",
        help_text="Centres this person works at or looks after. An agent with none ticked covers every centre.",
    )
    departments = models.ManyToManyField(
        Department, blank=True, related_name="members",
        help_text="Only for agents and department heads: the teams they work in.",
    )
    photo = models.FileField(upload_to="people/", blank=True,
                             help_text="Optional. A square photo looks best. People can also set this themselves.")

    class Meta:
        ordering = ["first_name", "username"]

    @property
    def display_name(self):
        return self.get_full_name() or self.username

    @property
    def initials(self):
        parts = self.display_name.split()
        return (parts[0][:1] + (parts[-1][:1] if len(parts) > 1 else "")).upper()

    @property
    def hue(self):
        """A stable colour for this person's initials, so people are easy to tell apart."""
        return (self.pk or 0) * 47 % 360

    @property
    def role_label(self):
        return self.get_role_display().split(":")[0]

    @property
    def is_admin(self):
        return self.is_superuser or self.role == self.ADMIN

    @property
    def sees_everything(self):
        return self.is_admin or self.role == self.MANAGEMENT

    @property
    def insurance_level(self):
        """'admin', 'manage', 'all', 'own' or '' (no access). Only administrators and people given access see Insurance."""
        return "admin" if self.is_admin else self.insurance_access

    @property
    def can_view_insurance(self):
        return bool(self.insurance_level)

    @property
    def can_manage_insurance(self):
        return self.insurance_level in ("admin", "manage")

    @property
    def insurance_sees_all(self):
        return self.insurance_level in ("admin", "manage", "all")

    @property
    def insurance_money(self):
        """Sum insured and premium: administrators, Manage level, and management with access."""
        return bool(self.is_admin or self.insurance_access == "manage"
                    or (self.role == self.MANAGEMENT and self.insurance_access))

    @property
    def is_management_only(self):
        """Management that does not also administer: oversees, does not work or raise tickets day to day."""
        return self.role == self.MANAGEMENT and not self.is_superuser

    @property
    def is_resolver(self):
        return self.role in (self.AGENT, self.DEPT_HEAD) or self.is_admin

    def __str__(self):
        return self.display_name


class Vendor(models.Model):
    name = models.CharField(max_length=120, unique=True)
    contact_person = models.CharField(max_length=80, blank=True)
    phone = models.CharField(max_length=30, blank=True)
    email = models.EmailField(blank=True)
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


class Equipment(models.Model):
    centre = models.ForeignKey(Centre, on_delete=models.PROTECT, related_name="equipment")
    name = models.CharField(max_length=120, help_text="For example: MRI 1.5T, CT 128 slice.")
    make = models.CharField(max_length=80, blank=True)
    model = models.CharField(max_length=80, blank=True)
    serial_no = models.CharField("serial number", max_length=80, blank=True)
    vendor = models.ForeignKey(Vendor, null=True, blank=True, on_delete=models.SET_NULL)
    amc_expiry = models.DateField("AMC expiry date", null=True, blank=True)
    active = models.BooleanField(default=True)
    amc_alert_for = models.DateField(null=True, blank=True, editable=False)
    amc_alert_stage = models.PositiveSmallIntegerField(default=0, editable=False)
    contract_start = models.DateField("contract start date", null=True, blank=True)
    uptime_guarantee_percent = models.DecimalField(
        "uptime guarantee %", max_digits=5, decimal_places=2, default=95.0,
        help_text="Contractual uptime guarantee (e.g. 95.00% or 98.00%).",
    )
    penalty_rate_per_day = models.DecimalField(
        "penalty per day (₹)", max_digits=10, decimal_places=2, default=0.00,
        help_text="Fixed deduction per day of excess downtime beyond allowable limits.",
    )
    value = models.DecimalField(
        "value (₹)", max_digits=14, decimal_places=0, null=True, blank=True,
        help_text="Optional. Purchase or replacement value. Used to flag valuable machines that have no insurance.")
    annual_camc_cost = models.DecimalField(
        "annual CAMC cost (₹)", max_digits=12, decimal_places=2, default=0.00,
        help_text="Annual contract value for pro-rata penalty calculation if daily penalty is not set.",
    )

    class Meta:
        ordering = ["centre__name", "name"]
        verbose_name_plural = "equipment"

    def __str__(self):
        return f"{self.name} ({self.centre.code})"


class PreventiveMaintenance(models.Model):
    FREQUENCY_CHOICES = [
        (1, "Monthly (every month)"),
        (3, "Quarterly (every 3 months)"),
        (6, "Half-yearly (every 6 months)"),
        (12, "Annual (every 12 months)"),
    ]
    equipment = models.ForeignKey(Equipment, on_delete=models.CASCADE, related_name="pm_schedules")
    title = models.CharField(max_length=160, help_text="For example: Quarterly Calibration & Quality Assurance")
    frequency_months = models.PositiveSmallIntegerField("frequency", choices=FREQUENCY_CHOICES, default=3)
    last_performed_at = models.DateField("last completed", null=True, blank=True)
    next_due_date = models.DateField("next due date", help_text="Target date for next maintenance check.")
    advance_notice_days = models.PositiveSmallIntegerField(
        "advance notice (days)", default=7, help_text="Auto-raise a maintenance ticket this many days prior to due date."
    )
    assigned_to = models.ForeignKey(
        User, null=True, blank=True, on_delete=models.SET_NULL, related_name="pm_schedules",
        help_text="Optional designated engineer/agent."
    )
    last_ticket = models.ForeignKey(
        "Ticket", null=True, blank=True, on_delete=models.SET_NULL, related_name="pm_source",
        help_text="The latest ticket generated or linked to this PM schedule."
    )
    active = models.BooleanField(default=True)
    notes = models.TextField(blank=True, help_text="SOP checklist, service points, or guidelines.")
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ["next_due_date", "equipment__name"]
        verbose_name = "preventive maintenance schedule"

    def __str__(self):
        return f"{self.equipment.name} - {self.title} (Due: {self.next_due_date})"

    @property
    def is_overdue(self):
        return self.next_due_date < timezone.localdate()

    @property
    def days_until_due(self):
        return (self.next_due_date - timezone.localdate()).days


class SlaPolicy(models.Model):
    department = models.ForeignKey(
        Department, null=True, blank=True, on_delete=models.CASCADE,
        help_text="Leave empty for the default that applies to every department.",
    )
    priority = models.CharField(max_length=2, choices=Priority.choices)
    response_minutes = models.PositiveIntegerField("respond within (minutes)")
    resolution_minutes = models.PositiveIntegerField("resolve within (minutes)")

    class Meta:
        ordering = ["department__name", "priority"]
        unique_together = [("department", "priority")]
        verbose_name = "SLA time"

    def __str__(self):
        return f"{self.department or 'All departments'} {self.priority}"


class EscalationRule(models.Model):
    RESPONSE, RESOLUTION = "response", "resolution"
    BASIS = [(RESPONSE, "Response time"), (RESOLUTION, "Resolution time")]
    name = models.CharField(max_length=120)
    basis = models.CharField("clock", max_length=12, choices=BASIS, default=RESOLUTION)
    percent = models.PositiveIntegerField(
        "percent of time used", default=100,
        help_text="75 = warn before the deadline. 100 = at the deadline. 150 = half as long again after it.",
    )
    priorities = models.CharField(max_length=20, blank=True,
                                  help_text="Leave empty for all. Or list, for example: P1,P2")
    notify_assignee = models.BooleanField("alert the assigned agent", default=False)
    notify_dept_head = models.BooleanField("alert the department head", default=True)
    notify_centre_manager = models.BooleanField("alert the centre manager", default=False)
    notify_area_manager = models.BooleanField("alert the area manager", default=False)
    active = models.BooleanField(default=True)

    class Meta:
        ordering = ["basis", "percent"]

    def __str__(self):
        return self.name

    def applies_to(self, priority):
        wanted = [p.strip().upper() for p in self.priorities.split(",") if p.strip()]
        return not wanted or priority in wanted


# --------------------------------------------------------------------------
# Tickets
# --------------------------------------------------------------------------
class Status(models.TextChoices):
    NEW = "new", "New"
    ASSIGNED = "assigned", "Assigned"
    IN_PROGRESS = "in_progress", "In progress"
    ON_HOLD = "on_hold", "On hold"
    RESOLVED = "resolved", "Resolved"
    CLOSED = "closed", "Closed"
    CANCELLED = "cancelled", "Cancelled"


OPEN_STATUSES = [Status.NEW, Status.ASSIGNED, Status.IN_PROGRESS, Status.ON_HOLD]
RUNNING_STATUSES = [Status.NEW, Status.ASSIGNED, Status.IN_PROGRESS]

HOLD_REASONS = [
    ("requester", "Awaiting requester"),
    ("vendor", "Awaiting vendor"),
    ("parts", "Awaiting parts"),
]


class Ticket(models.Model):
    number = models.CharField(max_length=16, unique=True, editable=False)
    centre = models.ForeignKey(Centre, on_delete=models.PROTECT, related_name="tickets")
    category = models.ForeignKey(Category, on_delete=models.PROTECT, related_name="tickets")
    department = models.ForeignKey(Department, on_delete=models.PROTECT, related_name="tickets")
    requester = models.ForeignKey(User, on_delete=models.PROTECT, related_name="raised")
    assignee = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL, related_name="assigned")
    priority = models.CharField(max_length=2, choices=Priority.choices, default=Priority.P3)
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.NEW)
    subject = models.CharField(max_length=160)
    description = models.TextField()
    equipment = models.ForeignKey(Equipment, null=True, blank=True, on_delete=models.SET_NULL, related_name="tickets")
    pm_schedule = models.ForeignKey("PreventiveMaintenance", null=True, blank=True, on_delete=models.SET_NULL, related_name="tickets")
    patient_impact = models.BooleanField(default=False)
    extra = models.JSONField(default=dict, blank=True)

    hold_reason = models.CharField(max_length=12, choices=HOLD_REASONS, blank=True)
    resolution_note = models.TextField(blank=True)
    rating = models.PositiveSmallIntegerField(null=True, blank=True)
    priority_raised = models.BooleanField(default=False)
    reopen_count = models.PositiveSmallIntegerField(default=0)

    # SLA clock (24x7). Times on hold are added to both deadlines.
    sla_started_at = models.DateTimeField()
    response_minutes = models.PositiveIntegerField()
    resolution_minutes = models.PositiveIntegerField()
    response_due = models.DateTimeField()
    resolution_due = models.DateTimeField()
    first_response_at = models.DateTimeField(null=True, blank=True)
    hold_started_at = models.DateTimeField(null=True, blank=True)
    hold_seconds = models.PositiveIntegerField(default=0)
    response_breached = models.BooleanField(default=False)
    resolution_breached = models.BooleanField(default=False)

    created_at = models.DateTimeField(default=timezone.now, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)
    resolved_at = models.DateTimeField(null=True, blank=True)
    closed_at = models.DateTimeField(null=True, blank=True)
    is_archived = models.BooleanField(default=False, db_index=True)
    archived_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["status", "department"]), models.Index(fields=["centre", "status"])]

    def __str__(self):
        return self.number

    @property
    def is_open(self):
        return self.status in OPEN_STATUSES

    def elapsed_seconds(self, now=None):
        """Working seconds used on the SLA clock, excluding time on hold."""
        now = now or timezone.now()
        end = self.resolved_at if self.resolved_at and not self.is_open else now
        if self.hold_started_at:
            end = min(end, self.hold_started_at)
        return max(0, (end - self.sla_started_at).total_seconds() - self.hold_seconds)

    def percent_used(self, basis="resolution", now=None):
        minutes = self.resolution_minutes if basis == "resolution" else self.response_minutes
        return 100.0 * self.elapsed_seconds(now) / max(1, minutes * 60)

    @property
    def sla_state(self):
        """ok, warn, breach, paused or done: drives the countdown colour."""
        if self.status == Status.ON_HOLD:
            return "paused"
        if not self.is_open:
            return "breach" if self.resolution_breached else "done"
        pct = self.percent_used()
        return "breach" if pct >= 100 else "warn" if pct >= 75 else "ok"

    @property
    def sla_percent(self):
        return min(100, round(self.percent_used()))


def attachment_path(instance, filename):
    return f"tickets/{instance.ticket.number}/{timezone.now():%Y%m%d%H%M%S}-{filename}"


class TicketComment(models.Model):
    ticket = models.ForeignKey(Ticket, on_delete=models.CASCADE, related_name="comments")
    author = models.ForeignKey(User, on_delete=models.PROTECT)
    body = models.TextField()
    internal = models.BooleanField(default=False)
    created_at = models.DateTimeField(default=timezone.now)
    edited_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["created_at"]


class TicketAttachment(models.Model):
    ticket = models.ForeignKey(Ticket, on_delete=models.CASCADE, related_name="attachments")
    comment = models.ForeignKey(TicketComment, null=True, blank=True, on_delete=models.CASCADE,
                                related_name="attachments")
    file = models.FileField(upload_to=attachment_path, max_length=300)
    name = models.CharField(max_length=200)
    size = models.PositiveIntegerField(default=0)
    uploaded_by = models.ForeignKey(User, on_delete=models.PROTECT)
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ["created_at"]

    @property
    def is_image(self):
        return self.name.lower().rsplit(".", 1)[-1] in ("jpg", "jpeg", "png", "gif", "webp")


class TicketHistory(models.Model):
    ticket = models.ForeignKey(Ticket, on_delete=models.CASCADE, related_name="history")
    actor = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL)
    text = models.CharField(max_length=300)
    field = models.CharField(max_length=30, blank=True)
    old = models.CharField(max_length=200, blank=True)
    new = models.CharField(max_length=200, blank=True)
    note = models.TextField(blank=True)
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ["created_at", "id"]
        verbose_name_plural = "ticket history"


class DowntimeLog(models.Model):
    equipment = models.ForeignKey(Equipment, on_delete=models.CASCADE, related_name="downtime")
    ticket = models.OneToOneField(Ticket, on_delete=models.CASCADE, related_name="downtime")
    start = models.DateTimeField()
    end = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-start"]

    def seconds(self, now=None, since=None):
        end = self.end or now or timezone.now()
        start = max(self.start, since) if since else self.start
        return max(0, (end - start).total_seconds())


class VendorCall(models.Model):
    """A service call to an outside vendor for a ticket, to measure how fast vendors respond."""
    ticket = models.ForeignKey(Ticket, on_delete=models.CASCADE, related_name="vendor_calls")
    vendor = models.ForeignKey(Vendor, on_delete=models.PROTECT, related_name="calls")
    called_at = models.DateTimeField()
    reference = models.CharField("vendor's complaint number", max_length=60, blank=True)
    engineer = models.CharField(max_length=80, blank=True)
    attended_at = models.DateTimeField(null=True, blank=True)
    notes = models.TextField(blank=True)
    logged_by = models.ForeignKey(User, on_delete=models.PROTECT)

    class Meta:
        ordering = ["-called_at"]

    def response_seconds(self, now=None):
        end = self.attended_at or now or timezone.now()
        return max(0, (end - self.called_at).total_seconds())


class EscalationLog(models.Model):
    ticket = models.ForeignKey(Ticket, on_delete=models.CASCADE, related_name="escalations")
    rule = models.ForeignKey(EscalationRule, on_delete=models.CASCADE)
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        unique_together = [("ticket", "rule")]


# --------------------------------------------------------------------------
# Alerts
# --------------------------------------------------------------------------
class Notification(models.Model):
    KINDS = ["raised", "assigned", "started", "hold", "resolved", "closed", "reopened", "cancelled",
             "transferred", "priority", "reply", "note", "mention", "escalation", "vendor", "amc", "insurance", "other"]
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="notifications")
    ticket = models.ForeignKey(Ticket, null=True, blank=True, on_delete=models.CASCADE)
    kind = models.CharField(max_length=12, default="other")
    link = models.CharField(max_length=200, blank=True)
    title = models.CharField(max_length=200)
    body = models.CharField(max_length=400, blank=True)
    urgent = models.BooleanField(default=False)
    read = models.BooleanField(default=False)
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ["-created_at"]


class OutboundMessage(models.Model):
    EMAIL, SMS, WHATSAPP, TELEGRAM = "email", "sms", "whatsapp", "telegram"
    CHANNELS = [(EMAIL, "Email"), (SMS, "SMS"), (WHATSAPP, "WhatsApp"), (TELEGRAM, "Telegram")]
    PENDING, SENT, FAILED = "pending", "sent", "failed"
    channel = models.CharField(max_length=12, choices=CHANNELS)
    to = models.CharField(max_length=200)
    subject = models.CharField(max_length=200, blank=True)
    body = models.TextField()
    provider_template = models.CharField(max_length=120, blank=True)
    status = models.CharField(max_length=10, default=PENDING,
                              choices=[(PENDING, "Waiting"), (SENT, "Sent"), (FAILED, "Failed")])
    error = models.CharField(max_length=300, blank=True)
    attempts = models.PositiveSmallIntegerField(default=0)
    created_at = models.DateTimeField(default=timezone.now)
    sent_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "sent alert"


# --------------------------------------------------------------------------
# Insurance
# --------------------------------------------------------------------------
class InsuranceCoverType(models.Model):
    name = models.CharField(max_length=80, unique=True, help_text="For example: Fire, Building, Public liability.")
    core = models.BooleanField(
        "expected at every centre", default=False,
        help_text="Shown as a column on the coverage gaps tab, so a centre without this cover stands out.")
    active = models.BooleanField(default=True, help_text="Untick to stop offering it for new policies.")

    class Meta:
        ordering = ["name"]
        verbose_name = "insurance cover type"

    def __str__(self):
        return self.name


def insurance_path(instance, filename):
    return f"insurance/{instance.policy_id}/{timezone.now():%Y%m%d%H%M%S}-{filename}"


class InsurancePolicy(models.Model):
    ACTIVE, EXPIRING, EXPIRED, RENEWED, CANCELLED = "active", "expiring", "expired", "renewed", "cancelled"
    STATUS_LABELS = {ACTIVE: "Active", EXPIRING: "Expiring soon", EXPIRED: "Expired",
                     RENEWED: "Renewed", CANCELLED: "Cancelled"}

    centres = models.ManyToManyField(
        Centre, blank=True, related_name="insurance_policies",
        help_text="Choose every centre the policy covers. Leave empty for a policy that covers all centres or the head office.")
    cover_type = models.ForeignKey(InsuranceCoverType, on_delete=models.PROTECT, related_name="policies")
    insurer = models.CharField(max_length=120)
    policy_number = models.CharField(max_length=80)
    contact = models.CharField("broker or agent", max_length=160, blank=True,
                               help_text="Name and phone number, if there is one.")
    start_date = models.DateField()
    expiry_date = models.DateField()
    sum_insured = models.DecimalField("sum insured (₹)", max_digits=14, decimal_places=2, null=True, blank=True)
    premium = models.DecimalField("premium (₹)", max_digits=12, decimal_places=2, null=True, blank=True)
    responsible = models.ManyToManyField(
        User, blank=True, related_name="insurance_policies",
        help_text="Optional. One or more people. They also receive the expiry reminders if they have Insurance access.")
    covers = models.CharField(
        max_length=200, blank=True,
        help_text="Optional. What is covered, for example: building and contents, MRI room, 2nd floor.")
    premium_due = models.DateField(
        "next premium due", null=True, blank=True,
        help_text="Optional. For policies paid in instalments. Reminders come 7 days before and on the day. Change it after each payment.")
    quote_alert_for = models.DateField(null=True, blank=True, editable=False)
    archived_at = models.DateTimeField(null=True, blank=True, editable=False,
                                       help_text="Set when the policy is archived: kept with its history, out of the working lists and reminders.")
    archived_by = models.ForeignKey("User", null=True, blank=True, on_delete=models.SET_NULL, related_name="+", editable=False)
    premium_alert_for = models.DateField(null=True, blank=True, editable=False)
    premium_alert_stage = models.PositiveSmallIntegerField(default=0, editable=False)
    remind_days = models.PositiveSmallIntegerField(
        "first reminder (days before expiry)", null=True, blank=True,
        help_text="Optional. Leave empty to use the number on the Settings page. Further reminders follow 30 and 7 days before, and on the expiry date.")
    equipment = models.ManyToManyField(Equipment, blank=True, related_name="insurance_policies",
                                       help_text="Optional. The machines this policy covers.")
    covers_all_machines = models.BooleanField(
        "covers every machine at these centres", default=False,
        help_text="Tick for a blanket policy that covers all the machines at the centres above (every centre if none is ticked) without listing them.")
    notes = models.TextField(blank=True)
    cancelled = models.BooleanField(default=False, help_text="Tick if the policy was cancelled before it ended.")
    renewed = models.BooleanField(default=False, editable=False)
    renewed_from = models.ForeignKey("self", null=True, blank=True, on_delete=models.SET_NULL,
                                     related_name="renewals", editable=False)
    alert_for = models.DateField(null=True, blank=True, editable=False)
    alert_stage = models.PositiveSmallIntegerField(default=0, editable=False)
    created_by = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL, related_name="+",
                                   editable=False)
    created_at = models.DateTimeField(default=timezone.now, editable=False)

    class Meta:
        ordering = ["expiry_date", "insurer"]
        verbose_name = "insurance policy"
        verbose_name_plural = "insurance policies"

    def __str__(self):
        return f"{self.cover_type} {self.policy_number}"

    def sync_premium_due(self):
        """With an instalment schedule, 'next premium due' is the earliest instalment not yet paid (empty when all are
        paid). Reminders and the Home card keep reading that one date. Without a schedule nothing changes."""
        if not self.instalments.exists():
            return
        nxt = self.instalments.filter(paid_on__isnull=True).order_by("due_date").first()
        due = nxt.due_date if nxt else None
        if due != self.premium_due:
            self.premium_due = due
            self.save(update_fields=["premium_due"])

    @property
    def location(self):
        """Names of the centres covered, or 'All centres'."""
        names = [c.name for c in self.centres.all()]
        return ", ".join(names) if names else "All centres"

    @property
    def current(self):
        """In force or lapsed without a replacement: not cancelled, not renewed and not archived."""
        return not (self.cancelled or self.renewed or self.archived_at)

    def status_on(self, today, soon_days):
        if self.cancelled:
            return self.CANCELLED
        if self.renewed:
            return self.RENEWED
        if self.expiry_date < today:
            return self.EXPIRED
        if (self.expiry_date - today).days <= soon_days:
            return self.EXPIRING
        return self.ACTIVE


class InsuranceDocument(models.Model):
    policy = models.ForeignKey(InsurancePolicy, on_delete=models.CASCADE, related_name="documents")
    file = models.FileField(upload_to=insurance_path, max_length=300)
    name = models.CharField(max_length=200)
    size = models.PositiveIntegerField(default=0)
    uploaded_by = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ["created_at"]

    @property
    def is_image(self):
        return self.name.lower().rsplit(".", 1)[-1] in ("jpg", "jpeg", "png", "webp")


class RenewalQuote(models.Model):
    """An insurer's quote for renewing a policy. One may be marked chosen; Renew then starts from it."""
    policy = models.ForeignKey(InsurancePolicy, on_delete=models.CASCADE, related_name="quotes")
    insurer = models.CharField(max_length=120)
    premium = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    sum_insured = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)
    valid_until = models.DateField("quote valid until", null=True, blank=True)
    notes = models.CharField(max_length=300, blank=True)
    chosen = models.BooleanField(default=False)
    created_by = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ["-chosen", "premium", "pk"]


class PremiumInstalment(models.Model):
    """One payment of a premium paid in instalments."""
    policy = models.ForeignKey(InsurancePolicy, on_delete=models.CASCADE, related_name="instalments")
    due_date = models.DateField("due on")
    amount = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    paid_on = models.DateField(null=True, blank=True)

    class Meta:
        ordering = ["due_date", "pk"]


def claim_path(instance, filename):
    return f"claims/{instance.claim_id}/{timezone.now():%Y%m%d%H%M%S}-{filename}"


class InsuranceClaim(models.Model):
    """A claim made on a policy: what happened, when, on which machine, and how it ended."""
    FILED, REVIEW, APPROVED, SETTLED, REJECTED, WITHDRAWN = "filed", "review", "approved", "settled", "rejected", "withdrawn"
    STATUSES = [(FILED, "Filed"), (REVIEW, "Under review"), (APPROVED, "Approved, awaiting payment"),
                (SETTLED, "Settled"), (REJECTED, "Rejected"), (WITHDRAWN, "Withdrawn")]
    OPEN = (FILED, REVIEW, APPROVED)
    policy = models.ForeignKey(InsurancePolicy, on_delete=models.CASCADE, related_name="claims")
    claim_number = models.CharField("claim number", max_length=60, blank=True, help_text="The insurer's reference, once you have it.")
    incident_date = models.DateField("date of the incident")
    claimed_on = models.DateField("date filed with the insurer", null=True, blank=True)
    equipment = models.ForeignKey("Equipment", null=True, blank=True, on_delete=models.SET_NULL, related_name="+",
                                  verbose_name="machine")
    description = models.CharField(max_length=300, help_text="What happened, in a line.")
    status = models.CharField(max_length=12, choices=STATUSES, default=FILED)
    amount_claimed = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)
    amount_settled = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)
    notes = models.TextField(blank=True)
    created_by = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    created_at = models.DateTimeField(default=timezone.now)
    status_changed_on = models.DateField(default=timezone.localdate, editable=False)
    claim_alerted_on = models.DateField(null=True, blank=True, editable=False)

    class Meta:
        ordering = ["-incident_date", "-pk"]

    STEPS = (FILED, REVIEW, APPROVED, SETTLED)

    @property
    def step(self):
        """Position on the way to settlement, 0 to 3; None for a claim that ended another way."""
        return self.STEPS.index(self.status) if self.status in self.STEPS else None

    @property
    def progress(self):
        """The four steps with where this claim is: [(label, done|current|todo)], empty when it ended another way."""
        cur = self.step
        if cur is None:
            return []
        return [(l, "done" if i < cur else "current" if i == cur else "todo")
                for i, l in enumerate(["Filed", "Review", "Approved", "Settled"])]

    @property
    def waiting_days(self):
        return (timezone.localdate() - self.status_changed_on).days if self.is_open else None

    @property
    def is_open(self):
        return self.status in self.OPEN


def licence_path(instance, filename):
    return f"licences/{instance.licence_id}/{timezone.now():%Y%m%d%H%M%S}-{filename}"


class Licence(models.Model):
    """A licence, registration or certificate that runs out and must be renewed: AERB, biomedical waste, fire NOC and so on."""
    ACTIVE, EXPIRING, EXPIRED, RENEWED, CLOSED = "active", "expiring", "expired", "renewed", "closed"
    STATUS_LABELS = {ACTIVE: "Valid", EXPIRING: "Expiring soon", EXPIRED: "Expired", RENEWED: "Renewed", CLOSED: "Closed"}
    DEFAULT_FIRST_REMINDER = 90
    SUGGESTIONS = ["AERB licence (radiation)", "AERB registration of equipment", "Biomedical waste authorisation",
                   "Fire NOC", "Pollution control board consent", "Clinical establishment registration",
                   "Trade licence", "Lift licence", "NABL accreditation", "NABH accreditation", "PC-PNDT registration",
                   "Radiation safety officer approval", "Lead apron / TLD calibration certificate"]
    name = models.CharField("licence or certificate", max_length=120)
    issuer = models.CharField("issued by", max_length=120, blank=True, help_text="The authority, for example AERB or the pollution control board.")
    reference_number = models.CharField("number", max_length=80, blank=True)
    centres = models.ManyToManyField(Centre, blank=True, related_name="licences",
                                     help_text="Leave empty for one that covers every centre or the head office.")
    equipment = models.ForeignKey("Equipment", null=True, blank=True, on_delete=models.SET_NULL, related_name="+",
                                  verbose_name="machine", help_text="Only if it belongs to one machine.")
    issued_on = models.DateField("issued on")
    valid_until = models.DateField("valid until")
    responsible = models.ManyToManyField(User, blank=True, related_name="licences",
                                         help_text="Optional. One or more people. They also receive the reminders if they have Insurance access.")
    remind_days = models.PositiveSmallIntegerField("first reminder (days before it ends)", null=True, blank=True,
                                                   help_text="Optional. Empty uses 90 days. Then 30, 7, 0 and the day after.")
    notes = models.TextField(blank=True)
    closed = models.BooleanField("no longer needed", default=False)
    renewed = models.BooleanField(default=False)
    renewed_from = models.ForeignKey("self", null=True, blank=True, on_delete=models.SET_NULL, related_name="renewals")
    alert_for = models.DateField(null=True, blank=True, editable=False)
    alert_stage = models.PositiveSmallIntegerField(default=0, editable=False)
    archived_at = models.DateTimeField(null=True, blank=True, editable=False)
    archived_by = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL, related_name="+", editable=False)
    created_by = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ["valid_until", "name", "pk"]

    def __str__(self):
        return f"{self.name} {self.reference_number}".strip()

    @property
    def location(self):
        names = [c.name for c in self.centres.all()]
        return ", ".join(names) if names else "All centres"

    @property
    def current(self):
        return not (self.closed or self.renewed or self.archived_at)

    @property
    def first_reminder(self):
        return self.remind_days or self.DEFAULT_FIRST_REMINDER

    def status_on(self, today):
        if self.closed:
            return self.CLOSED
        if self.renewed:
            return self.RENEWED
        if self.valid_until < today:
            return self.EXPIRED
        if (self.valid_until - today).days <= self.first_reminder:
            return self.EXPIRING
        return self.ACTIVE


class LicenceDocument(models.Model):
    licence = models.ForeignKey(Licence, on_delete=models.CASCADE, related_name="documents")
    file = models.FileField(upload_to=licence_path, max_length=300)
    name = models.CharField(max_length=200)
    size = models.PositiveIntegerField(default=0)
    uploaded_by = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ["created_at", "pk"]

    @property
    def is_image(self):
        return self.name.lower().rsplit(".", 1)[-1] in ("jpg", "jpeg", "png", "webp")


class InsuranceLog(models.Model):
    """Who changed what on a policy. Shown on the policy page to everyone who may open it."""
    policy = models.ForeignKey(InsurancePolicy, null=True, blank=True, on_delete=models.SET_NULL, related_name="log")
    label = models.CharField(max_length=250, blank=True, help_text="What the policy was called, kept if it is deleted for good.")
    actor = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    at = models.DateTimeField(default=timezone.now)
    text = models.CharField(max_length=500)

    class Meta:
        ordering = ["-at", "-pk"]


class ClaimDocument(models.Model):
    """A claim form, photo, estimate or the insurer's letter. Optional on a claim."""
    claim = models.ForeignKey(InsuranceClaim, on_delete=models.CASCADE, related_name="documents")
    file = models.FileField(upload_to=claim_path, max_length=300)
    name = models.CharField(max_length=200)
    size = models.PositiveIntegerField(default=0)
    uploaded_by = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ["created_at", "pk"]

    @property
    def is_image(self):
        return self.name.lower().rsplit(".", 1)[-1] in ("jpg", "jpeg", "png", "webp")


class LicenceLog(models.Model):
    """Who changed what on a licence or certificate. Shown on its page to everyone who may open it."""
    licence = models.ForeignKey(Licence, null=True, blank=True, on_delete=models.SET_NULL, related_name="log")
    label = models.CharField(max_length=250, blank=True, help_text="What the licence was called, kept if it is deleted for good.")
    actor = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    at = models.DateTimeField(default=timezone.now)
    text = models.CharField(max_length=500)

    class Meta:
        ordering = ["-at", "-pk"]


class SiteSettings(models.Model):
    """One row only. Edited on the Settings screen."""
    org_name = models.CharField("organisation name", max_length=120, default="Pramodini Medicare Limited")
    site_url = models.CharField(
        "address of this system", max_length=200, blank=True,
        help_text="Used for links in emails and on equipment QR labels, for example http://diagtrack.local")
    logo = models.FileField("logo mark (square)", upload_to="brand/", blank=True,
                            help_text="Optional. Replaces the P mark in the menu and browser tab. PNG or SVG, square.")
    logo_wide = models.FileField("full logo (wide)", upload_to="brand/", blank=True,
                                 help_text="Optional. Replaces the full logo on the sign-in page and on printed reports.")
    brand_color = models.CharField(
        "brand colour", max_length=7, default="#274a99",
        validators=[RegexValidator(r"^#[0-9a-fA-F]{6}$", "Use a colour code like #274a99.")],
        help_text="Buttons and highlights. Choose a medium-dark colour so white text on it stays readable.")
    brand_color_2 = models.CharField(
        "second brand colour", max_length=7, default="#24aee8",
        validators=[RegexValidator(r"^#[0-9a-fA-F]{6}$", "Use a colour code like #24aee8.")],
        help_text="The glow on the home screen and sign-in page.")

    inapp_enabled = models.BooleanField("In-app alerts (bell)", default=True)
    email_enabled = models.BooleanField("Email alerts", default=False)
    sms_enabled = models.BooleanField("SMS alerts", default=False)
    whatsapp_enabled = models.BooleanField("WhatsApp alerts", default=False)
    telegram_enabled = models.BooleanField("Telegram alerts", default=False)
    area_manager_all = models.BooleanField("Alert the area manager on every new ticket", default=True)
    managers_on_close = models.BooleanField("Alert centre and area managers when a ticket is closed", default=True)
    popups_enabled = models.BooleanField("Pop-up messages on screen", default=True)
    amc_alert_days = models.PositiveSmallIntegerField("warn about AMC expiry (days before)", default=60)
    insurance_alert_days = models.PositiveSmallIntegerField(
        "warn about insurance expiry (days before)", default=60,
        help_text="First reminder. Further reminders go out 30 and 7 days before, and on the expiry date.")
    insurance_min_value = models.PositiveBigIntegerField(
        "flag uninsured machines worth at least (₹)", default=1000000,
        help_text="Machines with a value at or above this and no current policy listing them show on the Coverage grid and in the weekly summary. 0 switches the check off.")
    insurance_quote_days = models.PositiveSmallIntegerField(
        "ask for renewal quotes this many days before expiry", default=90,
        help_text="A reminder goes out when a policy is this close to ending and no renewal quote is recorded. 0 switches it off.")
    insurance_claim_days = models.PositiveSmallIntegerField(
        "chase an open claim after this many days in one status", default=14,
        help_text="A reminder goes out when a claim has stayed Filed, Under review or Approved this long, then again each time "
                  "the same number of days passes. 0 switches it off.")
    insurance_show_money = models.BooleanField(
        "show sum insured and premium on the Insurance page", default=True,
        help_text="Shown to administrators, people with Manage access and management with access. Untick to hide the money figures from everyone.")
    insurance_weekly_enabled = models.BooleanField(
        "weekly insurance summary email on Monday", default=True,
        help_text="Each administrator and each person with Insurance access gets what needs attention in what they may see. Nothing is sent when nothing needs attention.")
    last_insurance_summary_at = models.DateField(null=True, blank=True, editable=False)
    weekly_report_enabled = models.BooleanField("Weekly management email on Monday", default=True)
    last_weekly_report_at = models.DateField(null=True, blank=True, editable=False)

    auto_close_days = models.PositiveSmallIntegerField("close resolved tickets after (days)", default=3)
    reopen_days = models.PositiveSmallIntegerField("allow reopening for (days)", default=7)
    max_attachment_mb = models.PositiveSmallIntegerField("largest attachment (MB)", default=10)

    smtp_host = models.CharField("mail server", max_length=120, blank=True)
    smtp_port = models.PositiveIntegerField("port", default=587)
    smtp_user = models.CharField("user name", max_length=120, blank=True)
    smtp_password = EncryptedTextField("password", blank=True)
    smtp_security = models.CharField("security", max_length=8, default="tls",
                                     choices=[("tls", "STARTTLS (port 587)"), ("ssl", "SSL (port 465)"), ("none", "None")])
    from_email = models.CharField("send from", max_length=160, blank=True,
                                  help_text="For example: DiagTrack <helpdesk@yourdomain.in>")

    sms_url = models.CharField("SMS gateway address", max_length=400, blank=True)
    sms_headers = EncryptedTextField("SMS headers", blank=True,
                                   help_text="One per line, Name: value. For example  authkey: abc123")
    sms_body = models.TextField("SMS message format", blank=True,
                                help_text="What your provider expects. Use {to}, {message} and, if the provider needs one, {template} as placeholders.")
    whatsapp_url = models.CharField("WhatsApp gateway address", max_length=400, blank=True)
    whatsapp_headers = EncryptedTextField("WhatsApp headers", blank=True)
    whatsapp_body = models.TextField("WhatsApp message format", blank=True,
                                     help_text="What your provider expects. Use {to}, {message} and {template} as placeholders.")

    telegram_bot_token = EncryptedTextField("Telegram bot token", blank=True,
                                            help_text="From @BotFather, e.g. 123456:ABC-DEF1234ghIkl-zyx57W2v1u123ew11")
    telegram_default_chat_id = models.CharField("Default Telegram group/channel chat ID", max_length=60, blank=True,
                                                help_text="Optional. Group or channel ID (e.g. -100123456789) to receive urgent alerts.")

    class Meta:
        verbose_name_plural = "site settings"

    def save(self, *args, **kwargs):
        self.pk = 1
        super().save(*args, **kwargs)

    @classmethod
    def load(cls):
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj


class Heartbeat(models.Model):
    name = models.CharField(max_length=40, unique=True)
    at = models.DateTimeField()
    detail = models.CharField(max_length=300, blank=True)


class SignInLock(models.Model):
    """Wrong passwords counted per user name and per computer, so repeated guessing pauses sign-in."""
    key = models.CharField(max_length=200, unique=True)  # "user:<name>" or "ip:<address>"
    failures = models.PositiveIntegerField(default=0)
    first_failure = models.DateTimeField(default=timezone.now)
    locked_until = models.DateTimeField(null=True, blank=True)
    last_address = models.CharField(max_length=64, blank=True)

    class Meta:
        verbose_name = "sign-in lock"
        ordering = ["-locked_until", "-first_failure"]

    @property
    def who(self):
        kind, _, value = self.key.partition(":")
        return f"User name {value}" if kind == "user" else f"Computer {value}"

    def __str__(self):
        return self.who


class MessageTemplate(models.Model):
    """The wording of email, SMS and WhatsApp alerts, one row per event and channel.
    A missing row means the built-in wording in tickets/wording.py is used."""
    ALWAYS, URGENT, NEVER = "always", "urgent", "never"
    SEND = [(ALWAYS, "Always"), (URGENT, "Only urgent"), (NEVER, "Never")]
    event = models.CharField(max_length=20)
    channel = models.CharField(max_length=10, choices=OutboundMessage.CHANNELS)
    send = models.CharField(max_length=8, choices=SEND, default=ALWAYS)
    subject = models.CharField(max_length=200, blank=True)
    body = models.TextField()
    provider_template = models.CharField(
        "provider template", max_length=120, blank=True,
        help_text="Optional. The template name or DLT template ID your SMS or WhatsApp provider gave you. "
                  "Used where the gateway message format says {template}.")
    updated_at = models.DateTimeField(auto_now=True)
    updated_by = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")

    class Meta:
        unique_together = [("event", "channel")]
        ordering = ["event", "channel"]

    def __str__(self):
        return f"{self.event} {self.channel}"
