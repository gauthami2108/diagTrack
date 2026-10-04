"""
Automated checks for the rules in the blueprint.
Run with:  python manage.py test
"""
from datetime import timedelta
from unittest import mock

from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from . import notify, services
from .models import (Category, Centre, Department, Equipment, EscalationRule, Notification, OutboundMessage,
                     SiteSettings, SlaPolicy, Status, Ticket, User)


def at(moment):
    return mock.patch("django.utils.timezone.now", return_value=moment)


class Base(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command("setup_initial", verbosity=0)
        cls.kkp = Centre.objects.create(code="KKP", name="Kukatpally")
        cls.sec = Centre.objects.create(code="SEC", name="Secunderabad")
        cls.it = Department.objects.get(name="IT")
        cls.bio = Department.objects.get(name="Biomedical")
        cls.fac = Department.objects.get(name="Facilities")

        def user(name, role, centres=(), depts=()):
            u = User.objects.create_user(name, password="Demo@12345", role=role, first_name=name.title(),
                                         email=f"{name}@example.com", mobile="919999999999")
            u.centres.set(centres)
            u.departments.set(depts)
            return u

        cls.req = user("meena", User.REQUESTER, [cls.kkp])
        cls.other = user("divya", User.REQUESTER, [cls.sec])
        cls.it_head = user("srinivas", User.DEPT_HEAD, depts=[cls.it])
        cls.ravi = user("ravi", User.AGENT, depts=[cls.it])
        cls.farah = user("farah", User.AGENT, depts=[cls.it])
        cls.bio_head = user("anand", User.DEPT_HEAD, depts=[cls.bio])
        cls.naveen = user("naveen", User.AGENT, [cls.kkp], [cls.bio])
        cls.cm = user("imran", User.CENTRE_MANAGER, [cls.kkp])
        cls.am = user("kavitha", User.AREA_MANAGER, [cls.kkp, cls.sec])
        cls.director = user("director", User.MANAGEMENT)
        cls.it.head, cls.bio.head = cls.it_head, cls.bio_head
        cls.it.save(), cls.bio.save()
        cls.printer = Category.objects.get(name="Computer or printer")
        cls.aircon = Category.objects.get(name="Air conditioning")
        cls.mri_cat = Category.objects.get(name="MRI fault")
        cls.mri = Equipment.objects.create(centre=cls.kkp, name="MRI 1.5T")

    def raise_ticket(self, category=None, **kw):
        return services.create_ticket(self.req, self.kkp, category or self.printer, "Printer not printing",
                                      "Billing counter printer shows an error.", **kw)


class RoutingTests(Base):
    def test_number_carries_centre_prefix_and_counts_per_centre(self):
        a, b = self.raise_ticket(), self.raise_ticket()
        c = services.create_ticket(self.other, self.sec, self.printer, "x", "y")
        self.assertEqual([a.number, b.number, c.number], ["KKP-000001", "KKP-000002", "SEC-000001"])

    def test_category_decides_department(self):
        self.assertEqual(self.raise_ticket().department, self.it)
        self.assertEqual(self.raise_ticket(self.aircon).department, self.fac)

    def test_equipment_ticket_goes_to_biomedical(self):
        t = self.raise_ticket(self.printer, equipment=self.mri)
        self.assertEqual(t.department, self.bio)
        self.assertEqual(t.assignee, self.naveen)

    def test_round_robin_takes_turns(self):
        owners = [self.raise_ticket().assignee for _ in range(4)]
        self.assertEqual(owners, [self.ravi, self.farah, self.ravi, self.farah])

    def test_least_loaded_picks_the_freer_agent(self):
        self.it.assignment_mode = Department.LEAST_LOADED
        self.it.save()
        first = self.raise_ticket()
        second = self.raise_ticket()
        self.assertNotEqual(first.assignee, second.assignee)

    def test_manual_mode_leaves_ticket_new_and_alerts_head(self):
        self.it.assignment_mode = Department.MANUAL
        self.it.save()
        t = self.raise_ticket()
        self.assertEqual((t.status, t.assignee), (Status.NEW, None))
        self.assertTrue(Notification.objects.filter(user=self.it_head, ticket=t).exists())

    def test_no_agent_for_centre_leaves_it_with_the_head(self):
        t = services.create_ticket(self.other, self.sec, self.mri_cat, "MRI", "down")
        self.assertIsNone(t.assignee)  # naveen covers only KKP
        self.assertTrue(Notification.objects.filter(user=self.bio_head, ticket=t).exists())


class SlaTests(Base):
    def test_default_times(self):
        t = self.raise_ticket()  # P3
        self.assertEqual((t.response_minutes, t.resolution_minutes), (120, 1440))
        self.assertEqual(t.resolution_due - t.created_at, timedelta(hours=24))

    def test_department_override_wins(self):
        SlaPolicy.objects.create(department=self.it, priority="P3", response_minutes=60, resolution_minutes=600)
        self.assertEqual(self.raise_ticket().resolution_minutes, 600)

    def test_patient_impact_makes_p1_and_alerts_managers_urgently(self):
        s = SiteSettings.load()
        s.sms_enabled = s.email_enabled = True
        s.save()
        t = self.raise_ticket(patient_impact=True)
        self.assertEqual(t.priority, "P1")
        for person in (self.it_head, self.cm, self.am):
            self.assertTrue(Notification.objects.filter(user=person, ticket=t, urgent=True).exists(), person)
        self.assertTrue(OutboundMessage.objects.filter(channel="sms").exists())

    def test_hold_pauses_the_clock(self):
        t0 = timezone.now()
        with at(t0):
            t = self.raise_ticket()
            services.start_work(t, t.assignee)
        due = t.resolution_due
        with at(t0 + timedelta(hours=1)):
            services.hold(t, t.assignee, "vendor")
        with at(t0 + timedelta(hours=6)):
            self.assertEqual(services.run_sla_checks(), 0)  # nothing fires while paused
            services.resume(t, t.assignee)
        t.refresh_from_db()
        self.assertEqual(t.resolution_due - due, timedelta(hours=5))
        self.assertAlmostEqual(t.elapsed_seconds(t0 + timedelta(hours=7)), 2 * 3600, delta=2)

    def test_unaccepted_ticket_returns_to_head_and_escalates(self):
        t0 = timezone.now()
        with at(t0):
            t = self.raise_ticket()
        with at(t0 + timedelta(minutes=121)):
            services.run_sla_checks()
        t.refresh_from_db()
        self.assertTrue(t.response_breached)
        self.assertEqual(t.assignee, self.it_head)
        self.assertTrue(Notification.objects.filter(user=self.it_head, title__startswith="Response time missed").exists())

    def test_escalation_ladder_fires_once_per_rule(self):
        t0 = timezone.now()
        with at(t0):
            t = self.raise_ticket()
            services.start_work(t, t.assignee)
        with at(t0 + timedelta(hours=19)):  # 79% of 24h
            self.assertEqual(services.run_sla_checks(), 1)
            self.assertEqual(services.run_sla_checks(), 0)
        with at(t0 + timedelta(hours=25)):
            services.run_sla_checks()
        t.refresh_from_db()
        self.assertTrue(t.resolution_breached)
        self.assertTrue(Notification.objects.filter(user=self.cm, title__startswith="Resolution time missed").exists())
        self.assertFalse(Notification.objects.filter(user=self.am, title__startswith="Still open").exists())
        with at(t0 + timedelta(hours=37)):
            services.run_sla_checks()
        self.assertTrue(Notification.objects.filter(user=self.am, title__startswith="Still open").exists())

    def test_transfer_restarts_the_clock_for_new_department(self):
        t0 = timezone.now()
        with at(t0):
            t = self.raise_ticket()
        with at(t0 + timedelta(hours=3)):
            services.transfer(t, t.assignee, self.fac, "It is an electrical fault")
        t.refresh_from_db()
        self.assertEqual(t.department, self.fac)
        self.assertEqual(t.sla_started_at, t0 + timedelta(hours=3))
        self.assertTrue(t.history.filter(field="department").exists())

    def test_auto_close_after_three_days_and_reopen_window(self):
        t0 = timezone.now()
        with at(t0):
            t = self.raise_ticket()
            agent = t.assignee
            services.start_work(t, agent)
            services.resolve(t, agent, "Replaced the cable.")
        with at(t0 + timedelta(days=2)):
            self.assertEqual(services.auto_close(), 0)
        with at(t0 + timedelta(days=3, minutes=1)):
            self.assertEqual(services.auto_close(), 1)
        t.refresh_from_db()
        self.assertEqual(t.status, Status.CLOSED)
        with at(t0 + timedelta(days=6)):
            services.reopen(t, self.req, "Still not printing")
        t.refresh_from_db()
        self.assertEqual((t.status, t.assignee), (Status.IN_PROGRESS, agent))
        with at(t0 + timedelta(days=6, hours=1)):
            services.resolve(t, agent, "Fixed again.")
        with at(t0 + timedelta(days=14)):
            t.refresh_from_db()
            self.assertNotIn("reopen", services.allowed_actions(self.req, t))


class RuleTests(Base):
    def test_requester_can_raise_priority_once_with_a_reason(self):
        t = self.raise_ticket()
        with self.assertRaises(services.NotAllowed):
            services.change_priority(t, self.req, "P2", "")
        with self.assertRaises(services.NotAllowed):
            services.change_priority(t, self.req, "P4", "lower it")
        services.change_priority(t, self.req, "P2", "Queue is building at the counter")
        t.refresh_from_db()
        self.assertEqual((t.priority, t.resolution_minutes), ("P2", 480))
        with self.assertRaises(services.NotAllowed):
            services.change_priority(t, self.req, "P1", "again")
        services.change_priority(t, self.it_head, "P3", "Workaround in place")

    def test_resolve_needs_a_note_and_stops_downtime(self):
        t = self.raise_ticket(self.mri_cat, equipment=self.mri, equipment_down=True)
        services.start_work(t, self.naveen)
        with self.assertRaises(services.NotAllowed):
            services.resolve(t, self.naveen, "  ")
        services.resolve(t, self.naveen, "Cold head replaced.")
        self.assertIsNotNone(t.downtime.end)

    def test_requester_cancels_only_before_work_starts(self):
        t = self.raise_ticket()
        self.assertIn("cancel", services.allowed_actions(self.req, t))
        services.start_work(t, t.assignee)
        self.assertNotIn("cancel", services.allowed_actions(self.req, t))
        self.assertIn("cancel", services.allowed_actions(self.it_head, t))

    def test_internal_notes_do_not_alert_the_requester(self):
        t = self.raise_ticket()
        Notification.objects.all().delete()
        services.add_comment(t, t.assignee, "Check with vendor before replying", internal=True)
        self.assertFalse(Notification.objects.filter(user=self.req).exists())
        services.add_comment(t, t.assignee, "We are on it")
        self.assertTrue(Notification.objects.filter(user=self.req).exists())
        t.refresh_from_db()
        self.assertIsNotNone(t.first_response_at)

    def test_area_manager_alerted_on_every_ticket_unless_switched_off(self):
        t = self.raise_ticket()
        self.assertTrue(Notification.objects.filter(user=self.am, ticket=t).exists())
        s = SiteSettings.load()
        s.area_manager_all = False
        s.save()
        t2 = self.raise_ticket()
        self.assertFalse(Notification.objects.filter(user=self.am, ticket=t2).exists())

    def test_channel_switches(self):
        s = SiteSettings.load()
        s.inapp_enabled, s.email_enabled = False, True
        s.save()
        t = self.raise_ticket()
        self.assertFalse(Notification.objects.filter(ticket=t).exists())
        self.assertTrue(OutboundMessage.objects.filter(channel="email", to=t.assignee.email).exists())
        self.assertFalse(OutboundMessage.objects.filter(channel="sms").exists())  # not urgent, and SMS is off

    def test_failed_sends_are_retried_then_marked_failed(self):
        s = SiteSettings.load()
        s.email_enabled = True
        s.save()
        self.raise_ticket()
        for _ in range(notify.MAX_ATTEMPTS):
            notify.send_pending()
        self.assertFalse(OutboundMessage.objects.filter(status="pending").exists())
        self.assertIn("Mail server is empty", OutboundMessage.objects.first().error)


class VisibilityTests(Base):
    def setUp(self):
        self.t = self.raise_ticket()                                                   # KKP, IT
        self.t2 = services.create_ticket(self.other, self.sec, self.aircon, "AC", "x")  # SEC, Facilities

    def seen(self, user):
        return set(services.visible_tickets(user).values_list("number", flat=True))

    def test_who_sees_what(self):
        self.assertEqual(self.seen(self.req), {self.t.number})
        self.assertEqual(self.seen(self.ravi), {self.t.number})
        self.assertEqual(self.seen(self.naveen), set())
        self.assertEqual(self.seen(self.cm), {self.t.number})
        self.assertEqual(self.seen(self.am), {self.t.number, self.t2.number})
        self.assertEqual(self.seen(self.director), {self.t.number, self.t2.number})

    def test_management_is_view_only(self):
        self.assertEqual(services.allowed_actions(self.director, self.t), set())

    def test_pages_respect_visibility(self):
        self.client.force_login(self.other)
        self.assertEqual(self.client.get(reverse("ticket_detail", args=[self.t.number])).status_code, 404)
        self.assertEqual(self.client.get(reverse("dashboard")).status_code, 403)
        self.assertEqual(self.client.get(reverse("settings")).status_code, 403)
        self.client.force_login(self.req)
        self.assertEqual(self.client.get(reverse("ticket_detail", args=[self.t.number])).status_code, 200)

    def test_wrong_role_cannot_post_actions(self):
        self.client.force_login(self.req)
        self.client.post(reverse("ticket_action", args=[self.t.number, "resolve"]), {"note": "done"})
        self.t.refresh_from_db()
        self.assertNotEqual(self.t.status, Status.RESOLVED)


class ScreenTests(Base):
    """Every screen opens for a role that should see it, with demo data loaded."""

    def test_all_screens_render(self):
        admin = User.objects.filter(is_superuser=True).first()
        t = self.raise_ticket(self.mri_cat, equipment=self.mri, equipment_down=True)
        services.add_comment(t, self.naveen, "On my way")
        self.client.force_login(admin)
        for name, args in [("home", []), ("ticket_new", []), ("ticket_list", []), ("board", []),
                           ("ticket_detail", [t.number]), ("equipment_list", []),
                           ("equipment_detail", [self.mri.pk]), ("dashboard", []), ("notifications", []),
                           ("settings", []), ("password_change", []), ("export_csv", []), ("admin:index", [])]:
            resp = self.client.get(reverse(name, args=args))
            self.assertEqual(resp.status_code, 200, name)
        for user in (self.req, self.naveen, self.bio_head, self.cm, self.am, self.director):
            self.client.force_login(user)
            self.assertEqual(self.client.get(reverse("home")).status_code, 200, user)
            self.assertEqual(self.client.get(reverse("ticket_list")).status_code, 200, user)

    def test_raise_ticket_through_the_form(self):
        self.client.force_login(self.req)
        resp = self.client.post(reverse("ticket_new"), {
            "centre": self.kkp.pk, "category": self.mri_cat.pk, "subject": "MRI table stuck",
            "description": "Table does not move.", "equipment": self.mri.pk, "equipment_down": "on",
            f"field_{self.mri_cat.fields.first().pk}": "E204"})
        t = Ticket.objects.get()
        self.assertRedirects(resp, reverse("ticket_detail", args=[t.number]))
        self.assertEqual(t.extra, {"Error code or message": "E204"})
        self.assertTrue(hasattr(t, "downtime"))
        resp = self.client.post(reverse("ticket_new"), {"centre": self.kkp.pk})
        self.assertContains(resp, "The ticket was not raised")

    def test_full_journey_through_the_screens(self):
        self.client.force_login(self.req)
        self.client.post(reverse("ticket_new"), {"centre": self.kkp.pk, "category": self.printer.pk,
                                                 "subject": "Printer", "description": "Not printing"})
        t = Ticket.objects.get()
        agent = t.assignee
        self.client.force_login(agent)
        act = lambda a, d=None: self.client.post(reverse("ticket_action", args=[t.number, a]), d or {})
        act("start"), act("hold", {"reason": "parts", "note": "Ordered toner"}), act("resume")
        act("resolve", {"note": "New toner fitted."})
        self.client.force_login(self.req)
        act("close", {"rating": "5"})
        t.refresh_from_db()
        self.assertEqual((t.status, t.rating), (Status.CLOSED, 5))
        self.assertGreaterEqual(t.history.count(), 7)


class DashboardTests(Base):
    def test_status_and_priority_counts_add_up_for_a_department_head(self):
        from . import reports
        for _ in range(5):
            self.raise_ticket()
        t = self.raise_ticket()
        services.start_work(t, t.assignee)
        d = reports.dashboard(services.visible_tickets(self.it_head))
        self.assertEqual(d["head"]["open"], 6)
        self.assertEqual(sum(b["value"] for b in d["by_status"]), 6)
        self.assertEqual(sum(b["value"] for b in d["by_priority"]), 6)
        self.assertEqual(sum(b["value"] for b in d["by_centre"]), 6)
