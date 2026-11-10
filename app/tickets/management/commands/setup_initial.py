"""
First-run setup. Safe to run again: it only adds what is missing and never
overwrites changes made in the administration screens.
"""
import os

from django.core.management.base import BaseCommand

from tickets.models import (Category, CategoryField, Department, EscalationRule, SiteSettings,
                            SlaPolicy, User)
from tickets.services import DEFAULT_SLA

DEPARTMENTS = {
    "IT": ("round_robin", False, [
        ("Computer or printer", "P3"), ("Network or internet", "P2"), ("LIS, RIS or PACS", "P2"),
        ("Billing software", "P2"), ("Email and logins", "P4")]),
    "Biomedical": ("round_robin", True, [
        ("MRI fault", "P1"), ("CT fault", "P1"), ("X-ray fault", "P2"), ("Ultrasound fault", "P2"),
        ("Lab analyser fault", "P2"), ("ECG and other equipment", "P3"), ("Calibration", "P4")]),
    "Facilities": ("round_robin", False, [
        ("Electrical", "P2"), ("UPS or generator", "P1"), ("Air conditioning", "P3"), ("Plumbing", "P3"),
        ("Civil repairs", "P4"), ("Housekeeping", "P4")]),
    "Stores and purchase": ("manual", False, [
        ("Reagents", "P2"), ("Films and contrast", "P2"), ("Consumables", "P3"), ("Stationery", "P4")]),
    "HR and admin": ("manual", False, [
        ("Attendance", "P4"), ("ID cards", "P4"), ("Staffing shortage", "P3"), ("Transport", "P4")]),
    "Finance and billing": ("manual", False, [
        ("Refunds", "P3"), ("Cash handling", "P3"), ("Insurance or corporate billing", "P3")]),
    "Quality": ("manual", False, [
        ("Incident report", "P3"), ("Audit finding", "P3"), ("Document control", "P4")]),
}
EQUIPMENT_CATEGORIES = {"MRI fault", "CT fault", "X-ray fault", "Ultrasound fault", "Lab analyser fault",
                        "ECG and other equipment", "Calibration"}
RULES = [
    ("Resolution deadline close (75% used)", "resolution", 75, "", True, True, False, False),
    ("Response time missed", "response", 100, "", False, True, False, False),
    ("Resolution time missed", "resolution", 100, "", False, True, True, False),
    ("Still open well past the deadline", "resolution", 150, "", False, True, True, True),
]


class Command(BaseCommand):
    help = "Create the starting departments, categories, SLA times, escalation rules and the first administrator."

    def handle(self, *args, **opts):
        site = SiteSettings.load()
        if os.environ.get("SITE_URL") and not site.site_url:
            site.site_url = os.environ["SITE_URL"].rstrip("/")
            site.save(update_fields=["site_url"])
        if not Department.objects.exists():
            for name, (mode, equip, cats) in DEPARTMENTS.items():
                d = Department.objects.create(name=name, assignment_mode=mode, handles_equipment=equip)
                for cname, prio in cats:
                    c = Category.objects.create(name=cname, department=d, default_priority=prio,
                                                asks_equipment=cname in EQUIPMENT_CATEGORIES)
                    if cname in EQUIPMENT_CATEGORIES:
                        CategoryField.objects.create(category=c, label="Error code or message", order=1)
                    if cname == "Computer or printer":
                        CategoryField.objects.create(category=c, label="Room or counter", order=1)
                    if cname == "Billing software":
                        # No free-text box: the two questions below are the whole description.
                        c.asks_description = False
                        c.save(update_fields=["asks_description"])
                        CategoryField.objects.create(category=c, label="Bill Number", required=True, order=0)
                        CategoryField.objects.create(category=c, label="Remarks", kind="longtext", required=True, order=1)
            self.stdout.write("Created the starting departments and categories.")
        if not SlaPolicy.objects.exists():
            for prio, (resp, reso) in DEFAULT_SLA.items():
                SlaPolicy.objects.create(department=None, priority=prio, response_minutes=resp,
                                         resolution_minutes=reso)
            self.stdout.write("Created the default SLA times.")
        if not EscalationRule.objects.exists():
            for name, basis, pct, prios, a, h, c, am in RULES:
                EscalationRule.objects.create(name=name, basis=basis, percent=pct, priorities=prios,
                                              notify_assignee=a, notify_dept_head=h,
                                              notify_centre_manager=c, notify_area_manager=am)
            self.stdout.write("Created the escalation rules.")
        username = os.environ.get("ADMIN_USERNAME", "admin")
        if not User.objects.filter(is_superuser=True).exists():
            # Setup stores only a hash of the chosen password (ADMIN_PASSWORD_HASH), never the password itself.
            pw_hash = os.environ.get("ADMIN_PASSWORD_HASH", "")
            password = os.environ.get("ADMIN_PASSWORD") or ("" if pw_hash else "ChangeMe@123")
            user = User.objects.create_superuser(username=username, password=password or None, role=User.ADMIN,
                                                 first_name="System", last_name="Administrator")
            if pw_hash:
                user.password = pw_hash
                user.save(update_fields=["password"])
                self.stdout.write(f"Created the administrator login '{username}' with the password chosen in setup.")
            else:
                self.stdout.write(f"Created the administrator login '{username}'. Change its password after first login.")
