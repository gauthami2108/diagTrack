"""
Demo data for trying the system out: three centres, staff logins, equipment and
about fifty tickets spread over the last two weeks.

Never run this on the live system. All demo logins use the password Demo@12345.
"""
import random
from datetime import timedelta
from unittest import mock

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from tickets import services
from tickets.models import (Category, Centre, Department, Equipment, Notification, Status, Ticket,
                            TicketComment, TicketHistory, User, Vendor)

PASSWORD = "Demo@12345"
CENTRES = [("BJH", "Banjara Hills", "Hyderabad"), ("KKP", "Kukatpally", "Hyderabad"),
           ("SEC", "Secunderabad", "Hyderabad")]
EQUIPMENT = [("MRI 1.5T", "Siemens", "Magnetom Sempra"), ("CT 128 slice", "GE", "Revolution EVO"),
             ("Digital X-ray", "Fujifilm", "FDR Smart"), ("Ultrasound room 1", "Philips", "Affiniti 70"),
             ("Biochemistry analyser", "Roche", "Cobas c311"), ("ECG machine", "BPL", "Cardiart 9108")]
ISSUES = {
    "Computer or printer": ["Billing counter printer not printing", "Reception PC very slow",
                            "Report printer paper jam keeps coming back"],
    "Network or internet": ["Internet down at reception", "Wi-Fi dropping in reporting room"],
    "LIS, RIS or PACS": ["PACS images not loading for CT", "LIS not sending results to billing"],
    "Email and logins": ["New technician needs LIS login", "Password reset for front desk"],
    "MRI fault": ["MRI helium level alarm", "MRI table not moving"],
    "CT fault": ["CT tube warm-up failing", "CT gantry error on start"],
    "Ultrasound fault": ["Ultrasound probe image flickering"],
    "Lab analyser fault": ["Analyser reagent probe blocked", "Analyser QC failing on glucose"],
    "X-ray fault": ["X-ray detector not detected"],
    "Air conditioning": ["AC not cooling in MRI console room", "Water dripping from AC in waiting area"],
    "Electrical": ["Lights flickering in sample collection", "Socket sparking near billing counter"],
    "UPS or generator": ["UPS beeping, battery low"],
    "Plumbing": ["Tap leaking in patient washroom"],
    "Reagents": ["Thyroid reagent stock low"],
    "Films and contrast": ["X-ray films running out this week"],
    "Stationery": ["Need report covers and A4 paper"],
    "ID cards": ["ID card for new radiographer"],
    "Refunds": ["Refund pending for cancelled MRI"],
    "Incident report": ["Patient slipped near entrance, no injury"],
}
RESOLUTIONS = ["Restarted the service and checked with the user. Working now.",
               "Replaced the faulty part and tested twice.",
               "Vendor engineer visited, fault cleared and test scan done.",
               "Supplied from central stores.", "Reset and confirmed with the requester."]


class Command(BaseCommand):
    help = "Load demo centres, users, equipment and tickets."

    def add_arguments(self, parser):
        parser.add_argument("--force", action="store_true", help="Run even if tickets already exist.")

    def handle(self, *args, **opts):
        if Ticket.objects.exists() and not opts["force"]:
            raise CommandError("Tickets already exist. Demo data is only for an empty system.")
        if not Department.objects.exists():
            raise CommandError("Run setup_initial first.")
        rnd = random.Random(7)
        centres = [Centre.objects.get_or_create(code=c, defaults={"name": n, "city": city})[0]
                   for c, n, city in CENTRES]
        dept = {d.name: d for d in Department.objects.all()}

        def user(username, first, last, role, cents=(), depts=(), mobile=""):
            u, made = User.objects.get_or_create(username=username, defaults={
                "first_name": first, "last_name": last, "role": role, "mobile": mobile,
                "email": f"{username}@example.com"})
            if made:
                u.set_password(PASSWORD)
                u.save()
                u.centres.set(cents)
                u.departments.set([dept[d] for d in depts])
            return u

        user("kavitha", "Kavitha", "Rao", User.AREA_MANAGER, centres)
        user("director", "Ramesh", "Varma", User.MANAGEMENT)
        for c, (first, last) in zip(centres, [("Sunitha", "Reddy"), ("Imran", "Khan"), ("Priya", "Nair")]):
            user(f"mgr.{c.code.lower()}", first, last, User.CENTRE_MANAGER, [c])
        heads = {
            "IT": user("srinivas", "Srinivas", "Murthy", User.DEPT_HEAD, depts=["IT"]),
            "Biomedical": user("anand", "Anand", "Kulkarni", User.DEPT_HEAD, depts=["Biomedical"]),
            "Facilities": user("joseph", "Joseph", "Thomas", User.DEPT_HEAD, depts=["Facilities"]),
        }
        ops = user("lakshmi", "Lakshmi", "Iyer", User.DEPT_HEAD,
                   depts=["Stores and purchase", "HR and admin", "Finance and billing", "Quality"])
        for name, d in dept.items():
            d.head = heads.get(name, ops)
            d.save()
        user("ravi", "Ravi", "Teja", User.AGENT, depts=["IT"])
        user("farah", "Farah", "Sheikh", User.AGENT, depts=["IT"])
        user("naveen", "Naveen", "Goud", User.AGENT, centres[:2], ["Biomedical"])
        user("deepa", "Deepa", "Menon", User.AGENT, centres[1:], ["Biomedical"])
        user("suresh", "Suresh", "Babu", User.AGENT, depts=["Facilities"])
        requesters = [
            user("meena", "Meena", "Kumari", User.REQUESTER, [centres[0]]),
            user("arjun", "Arjun", "Das", User.REQUESTER, [centres[0]]),
            user("swathi", "Swathi", "Chowdary", User.REQUESTER, [centres[1]]),
            user("rahim", "Rahim", "Ali", User.REQUESTER, [centres[1]]),
            user("divya", "Divya", "Sharma", User.REQUESTER, [centres[2]]),
            user("kiran", "Kiran", "Yadav", User.REQUESTER, [centres[2]]),
        ]
        vendor = Vendor.objects.get_or_create(name="MediServe Engineering", defaults={
            "contact_person": "Service desk", "phone": "040 4000 0000"})[0]
        for c in centres:
            for name, make, model in EQUIPMENT:
                Equipment.objects.get_or_create(centre=c, name=name, defaults={
                    "make": make, "model": model, "vendor": vendor,
                    "serial_no": f"{c.code}-{rnd.randint(10000, 99999)}"})

        real_now = timezone.now()
        cats = {c.name: c for c in Category.objects.all()}
        made = 0

        def at(moment):
            return mock.patch("django.utils.timezone.now", return_value=moment)

        def backdate(t, since, moment):
            for model in (TicketHistory, TicketComment, Notification):
                model.objects.filter(ticket=t, created_at__gte=since).update(created_at=moment)

        plan = [(cat, text) for cat, texts in ISSUES.items() for text in texts]
        plan = plan + rnd.sample(plan, 18)
        rnd.shuffle(plan)
        for i, (cat_name, text) in enumerate(plan):
            cat = cats[cat_name]
            who = rnd.choice(requesters)
            centre = who.centres.first()
            age_hours = rnd.uniform(0.3, 14 * 24) if i > 9 else rnd.uniform(0.2, 9)
            created = real_now - timedelta(hours=age_hours)
            equipment = None
            if cat.asks_equipment:
                key = cat_name.split()[0]
                equipment = (Equipment.objects.filter(centre=centre, name__icontains=key).first()
                             or Equipment.objects.filter(centre=centre).first())
            impact = cat.default_priority == "P1" and rnd.random() < 0.7
            mark = timezone.now()
            with at(created):
                t = services.create_ticket(
                    who, centre, cat, text,
                    f"{text}. Noticed this at {timezone.localtime(created):%H:%M}. Please look into it.",
                    equipment=equipment, patient_impact=impact, equipment_down=bool(equipment) and impact,
                    extra={"Error code or message": f"E{rnd.randint(100, 999)}"} if equipment else {}, now=created)
            backdate(t, mark, created)
            made += 1
            # how far does this ticket get?
            outcome = rnd.random()
            if age_hours > 30:
                outcome = max(outcome, 0.35)  # older tickets were all dealt with
            if age_hours < 1.5 or outcome < 0.12:
                continue
            agent = t.assignee or t.department.head
            step = created + timedelta(minutes=rnd.randint(5, int(t.response_minutes * 1.3) + 6))
            if step > real_now:
                continue
            mark = timezone.now()
            with at(step):
                if t.assignee is None:
                    services.assign(t, t.department.head, agent, check=False)
                services.start_work(t, agent)
                services.add_comment(t, agent, "Looking into this now. Will update shortly.")
            backdate(t, mark, step)
            if outcome < 0.30 or age_hours < 4:
                if outcome < 0.2 and step + timedelta(minutes=30) < real_now:
                    mark = timezone.now()
                    with at(step + timedelta(minutes=30)):
                        services.hold(t, agent, rnd.choice(["vendor", "parts", "requester"]),
                                      "Waiting for the engineer visit.")
                    backdate(t, mark, step + timedelta(minutes=30))
                continue
            done = step + timedelta(minutes=rnd.randint(20, int(t.resolution_minutes * 1.15)))
            if age_hours > 30:
                done = min(done, real_now - timedelta(hours=rnd.uniform(2, 20)))
                done = max(done, step + timedelta(minutes=10))
            if done > real_now:
                continue
            mark = timezone.now()
            with at(done):
                services.resolve(t, agent, rnd.choice(RESOLUTIONS))
            backdate(t, mark, done)
            confirm = done + timedelta(hours=rnd.randint(1, 30))
            if confirm < real_now and rnd.random() < 0.75:
                mark = timezone.now()
                with at(confirm):
                    services.close(t, who, rating=rnd.choice([3, 4, 4, 5, 5]))
                backdate(t, mark, confirm)
        services.run_sla_checks()
        Notification.objects.filter(created_at__lt=real_now - timedelta(days=2)).update(read=True)
        self.stdout.write(f"Loaded {len(centres)} centres, {User.objects.count()} users, "
                          f"{Equipment.objects.count()} equipment items and {made} tickets. "
                          f"Demo password for every login: {PASSWORD}")
