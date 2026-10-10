"""
Automated checks for the rules in the blueprint.
Run with:  python manage.py test
"""
from datetime import timedelta
from unittest import mock

from django.conf import settings
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


class Phase2Tests(Base):
    """Vendor calls, AMC alerts, @mentions, comment edits, pop-up feed and the new screens."""

    def test_vendor_call_measures_response_time(self):
        from .models import Vendor
        vendor = Vendor.objects.create(name="MediServe")
        t0 = timezone.now()
        with at(t0):
            t = self.raise_ticket(self.mri_cat, equipment=self.mri, equipment_down=True)
            call = services.log_vendor_call(t, self.naveen, vendor, "CMP-77")
        with self.assertRaises(services.NotAllowed):
            services.log_vendor_call(t, self.req, vendor)  # requesters cannot
        with at(t0 + timedelta(hours=3)):
            services.vendor_attended(call, self.naveen, "Ramesh")
        call.refresh_from_db()
        self.assertEqual(call.response_seconds(), 3 * 3600)
        self.assertTrue(t.history.filter(text__contains="engineer attended after 3h 0m").exists())
        from . import reports
        perf = reports.vendor_performance()
        self.assertEqual((perf[0]["calls"], perf[0]["avg_hours"]), (1, 3.0))

    def test_amc_alerts_at_each_stage_once(self):
        today = timezone.localdate()
        self.mri.amc_expiry = today + timedelta(days=45)
        self.mri.save()
        self.assertEqual(services.amc_checks(today), 1)
        self.assertEqual(services.amc_checks(today), 0)  # not again the same day
        self.assertTrue(Notification.objects.filter(user=self.bio_head, kind="amc").exists())
        self.assertEqual(services.amc_checks(today + timedelta(days=16)), 1)  # 29 days left
        self.assertEqual(services.amc_checks(today + timedelta(days=46)), 1)  # expired
        self.mri.amc_expiry = today + timedelta(days=400)  # renewed
        self.mri.save()
        self.assertEqual(services.amc_checks(today + timedelta(days=47)), 0)

    def test_mentions_alert_only_people_who_can_see_the_ticket(self):
        t = self.raise_ticket()
        Notification.objects.all().delete()
        services.add_comment(t, t.assignee, "Can @srinivas approve? Also @divya and @nobody.")
        self.assertTrue(Notification.objects.filter(user=self.it_head, kind="mention").exists())
        self.assertFalse(Notification.objects.filter(user=self.other).exists())  # divya cannot see KKP IT tickets
        services.add_comment(t, t.assignee, "Internal for @meena", internal=True)
        self.assertFalse(Notification.objects.filter(user=self.req, kind="mention").exists())

    def test_comment_edit_by_author_within_a_day(self):
        t = self.raise_ticket()
        c, _ = services.add_comment(t, self.req, "Printer at counter 2")
        with self.assertRaises(services.NotAllowed):
            services.edit_comment(c, t.assignee, "changed")
        services.edit_comment(c, self.req, "Printer at counter 3")
        c.refresh_from_db()
        self.assertEqual(c.body, "Printer at counter 3")
        self.assertIsNotNone(c.edited_at)
        with at(timezone.now() + timedelta(hours=25)):
            with self.assertRaises(services.NotAllowed):
                services.edit_comment(c, self.req, "too late")

    def test_close_alerts_owner_head_and_managers(self):
        t = self.raise_ticket()
        services.start_work(t, t.assignee)
        services.resolve(t, t.assignee, "Done")
        Notification.objects.all().delete()
        services.close(t, self.req, 5)
        for person in (t.assignee, self.it_head, self.cm, self.am):
            self.assertTrue(Notification.objects.filter(user=person, kind="closed").exists(), person)
        self.assertFalse(Notification.objects.filter(user=self.req).exists())

    def test_rich_text_is_safe(self):
        from .templatetags.dt import rich
        html = rich('**bold** <script>alert(1)</script> [x](javascript:alert(1)) @srinivas')
        self.assertIn("<strong>bold</strong>", html)
        self.assertNotIn("<script>", html)
        self.assertNotIn('href="javascript', html)
        self.assertIn('class="mention">@Srinivas', html)

    def test_live_feed_returns_only_new_alerts_for_me(self):
        self.client.force_login(self.it_head)
        first = self.client.get(reverse("api_live")).json()
        t = services.create_ticket(self.req, self.kkp, self.printer, "x", "y", patient_impact=True)
        feed = self.client.get(reverse("api_live") + f"?after={first['last']}").json()
        self.assertTrue(any(i["kind"] == "raised" and i["number"] == t.number and i["urgent"] for i in feed["items"]))
        again = self.client.get(reverse("api_live") + f"?after={feed['last']}").json()
        self.assertEqual(again["items"], [])

    def test_alerts_page_draws_dot_text_and_time_in_their_columns(self):
        services.create_ticket(self.req, self.kkp, self.printer, "x", "y", patient_impact=True)
        self.client.force_login(self.it_head)
        html = self.client.get(reverse("notifications")).content.decode()
        self.assertRegex(html, r'class="alert-row alert-row-l[^"]*"[^>]*><span class="k k-raised"[^>]*></span><span><b>')

    def test_search_and_people_feeds_respect_visibility(self):
        t = self.raise_ticket()
        self.client.force_login(self.other)
        self.assertEqual(self.client.get(reverse("api_search") + "?q=Printer").json()["tickets"], [])
        self.client.force_login(self.req)
        self.assertEqual(self.client.get(reverse("api_search") + "?q=" + t.number).json()["tickets"][0]["number"], t.number)
        names = [p["username"] for p in self.client.get(reverse("api_people") + "?q=sri").json()["people"]]
        self.assertEqual(names, ["srinivas"])

    def test_new_screens_open(self):
        from .models import Vendor
        v = Vendor.objects.create(name="MediServe")
        t = self.raise_ticket(self.mri_cat, equipment=self.mri, equipment_down=True)
        services.log_vendor_call(t, self.naveen, v)
        self.client.force_login(self.am)
        for url in [reverse("centres"), reverse("centres") + "?days=7", reverse("equipment_list") + "?tab=faults",
                    reverse("equipment_list") + "?tab=vendors", reverse("equipment_list") + "?tab=amc",
                    reverse("equipment_labels"), reverse("equipment_qr", args=[self.mri.pk]), reverse("profile"),
                    reverse("ticket_detail", args=[t.number]) + "?preview=1",
                    reverse("ticket_new") + f"?equipment={self.mri.pk}"]:
            self.assertEqual(self.client.get(url).status_code, 200, url)
        resp = self.client.get(reverse("ticket_new") + f"?equipment={self.mri.pk}")
        self.assertContains(resp, "Scanned from the machine")
        self.assertContains(self.client.get(reverse("equipment_qr", args=[self.mri.pk])), "<svg")
        self.client.force_login(self.req)
        self.assertEqual(self.client.get(reverse("centres")).status_code, 403)

    def test_centre_matrix_numbers(self):
        from . import reports
        t = self.raise_ticket()
        services.start_work(t, t.assignee)
        services.resolve(t, t.assignee, "Done")
        self.raise_ticket(patient_impact=True)
        m = reports.centre_matrix(services.visible_tickets(self.director), [self.kkp, self.sec])
        kkp = m["rows"][0]
        self.assertEqual((kkp["open"], kkp["p1"], kkp["resolved"], kkp["sla_pct"]), (1, 1, 1, 100))
        self.assertEqual(m["totals"]["open"], 1)
        self.assertEqual(sum(c["n"] for row in m["grid"] for c in row["cells"]), 1)

    def test_ticket_page_actions_through_screens(self):
        from .models import Vendor
        v = Vendor.objects.create(name="MediServe")
        t = services.create_ticket(self.other, self.sec, self.mri_cat, "MRI", "down")  # nobody covers SEC
        self.client.force_login(self.bio_head)
        act = lambda a, d=None: self.client.post(reverse("ticket_action", args=[t.number, a]), d or {})
        act("assign_me")
        t.refresh_from_db()
        self.assertEqual(t.assignee, self.bio_head)
        t.equipment = self.mri
        t.save()
        act("vendor_call", {"vendor": v.pk, "reference": "R1"})
        call = t.vendor_calls.get()
        act("vendor_attended", {"call": call.pk, "engineer": "Ramesh"})
        call.refresh_from_db()
        self.assertEqual(call.engineer, "Ramesh")
        act("comment", {"body": "Checked **coil**"})
        c = t.comments.get()
        act("edit_comment", {"comment": c.pk, "body": "Checked the **coil**"})
        c.refresh_from_db()
        self.assertEqual(c.body, "Checked the **coil**")
        page = self.client.get(reverse("ticket_detail", args=[t.number]))
        self.assertContains(page, "<strong>coil</strong>")
        self.assertContains(page, "Edited")

    def test_uploaded_files_cannot_run_scripts(self):
        from django.core.files.uploadedfile import SimpleUploadedFile
        self.client.force_login(self.req)
        resp = self.client.post(reverse("profile"), {"first_name": "Meena", "photo": SimpleUploadedFile(
            "x.svg", b"<svg onload=alert(1)>", content_type="image/png")})
        self.assertContains(resp, "Choose a JPG, PNG or WEBP")
        t = services.create_ticket(self.req, self.kkp, self.printer, "x", "y", files=[SimpleUploadedFile(
            "a.png", b"\x89PNG fake", content_type="image/png")])
        resp = self.client.get(reverse("attachment", args=[t.attachments.get().pk]))
        self.assertIn("default-src 'none'", resp["Content-Security-Policy"])


class ReportTests(Base):
    def setUp(self):
        self.t1 = self.raise_ticket()                                                    # KKP, IT
        services.start_work(self.t1, self.t1.assignee)
        services.resolve(self.t1, self.t1.assignee, "Done")
        self.t2 = self.raise_ticket(self.aircon)                                          # KKP, Facilities
        self.t3 = services.create_ticket(self.other, self.sec, self.printer, "SEC printer", "x")  # SEC, IT

    def get(self, user, name, **params):
        from urllib.parse import urlencode
        self.client.force_login(user)
        return self.client.get(reverse(name) + "?" + urlencode(params, doseq=True))

    def test_grouping_and_filters(self):
        from . import exports
        from django.http import QueryDict
        r = exports.build_report(self.director, QueryDict("group=centre"))
        self.assertEqual([(m["labels"][0], m["raised"], m["resolved"]) for m in r["rows"]],
                         [("Kukatpally", 2, 1), ("Secunderabad", 1, 0)])
        self.assertEqual(r["totals"]["sla_pct"], 100)
        r = exports.build_report(self.director, QueryDict(f"group=department&centre={self.kkp.pk}"))
        self.assertEqual([(m["labels"][0], m["raised"]) for m in r["rows"]], [("Facilities", 1), ("IT", 1)])
        r = exports.build_report(self.director, QueryDict(f"group=centre_department&department={self.it.pk}"))
        self.assertEqual([m["labels"] for m in r["rows"]], [("Kukatpally", "IT"), ("Secunderabad", "IT")])
        self.assertEqual(r["pivot_totals"], [2])
        r = exports.build_report(self.cm, QueryDict("group=centre"))  # centre manager sees only KKP
        self.assertEqual([m["labels"][0] for m in r["rows"]], ["Kukatpally"])

    def test_period_choices(self):
        from . import exports
        from datetime import date
        today = date(2026, 10, 10)
        self.assertEqual(exports.period_bounds({"period": "lastmonth"}, today)[:2], (date(2026, 9, 1), date(2026, 9, 30)))
        self.assertEqual(exports.period_bounds({"period": "month"}, today)[:2], (date(2026, 10, 1), today))
        self.assertEqual(exports.period_bounds({"period": "custom", "from": "2026-10-05", "to": "2026-09-20"}, today)[:2],
                         (date(2026, 9, 20), date(2026, 10, 5)))

    def test_excel_download(self):
        import io
        from openpyxl import load_workbook
        resp = self.get(self.director, "report_excel", group="department", centre=[self.kkp.pk, self.sec.pk])
        self.assertEqual(resp.status_code, 200)
        self.assertIn("attachment", resp["Content-Disposition"])
        wb = load_workbook(io.BytesIO(resp.content))
        self.assertEqual(wb.sheetnames, ["Summary", "Centre x Department", "Tickets", "Missed deadlines",
                                         "Equipment downtime", "Vendor calls"])
        ws = wb["Summary"]
        values = [[c.value for c in row] for row in ws.iter_rows(min_row=5)]
        self.assertEqual(values[0][:3], ["Department", "Raised", "Resolved"])
        self.assertEqual([v[:3] for v in values[1:]], [["Facilities", 1, 0], ["IT", 2, 1], ["All", 3, 1]])
        self.assertEqual(wb["Tickets"].max_row, 4 + 3)

    def test_screens(self):
        self.assertContains(self.get(self.am, "reports", group="priority"), "By priority")
        resp = self.get(self.am, "report_print", group="centre")
        self.assertContains(resp, "Tickets by centre")
        self.assertContains(resp, "brand-logo.png")
        self.assertEqual(self.get(self.req, "reports").status_code, 403)
        self.assertEqual(self.get(self.req, "report_excel").status_code, 403)


class TemplateAndActionTests(Base):
    def setUp(self):
        self.admin = User.objects.create_user("boss", password="Demo@12345", role=User.ADMIN,
                                              email="boss@example.com", mobile="919888888888")
        s = SiteSettings.load()
        s.email_enabled = s.sms_enabled = s.whatsapp_enabled = True
        s.site_url = "http://diagtrack.local"
        s.save()

    def test_equipment_qr_is_a_real_svg_image_and_png(self):
        self.client.force_login(self.bio_head)
        svg = self.client.get(reverse("equipment_qr", args=[self.mri.pk]))
        self.assertEqual(svg["Content-Type"], "image/svg+xml")
        self.assertIn(b'xmlns="http://www.w3.org/2000/svg"', svg.content)
        png = self.client.get(reverse("equipment_qr", args=[self.mri.pk]) + "?format=png")
        self.assertTrue(png.content.startswith(b"\x89PNG"))
        page = self.client.get(reverse("equipment_detail", args=[self.mri.pk]))
        self.assertContains(page, f"http://diagtrack.local/tickets/new/?equipment={self.mri.pk}")

    def test_builtin_wording_fills_placeholders_and_drops_empty_lines(self):
        t = self.raise_ticket()
        mail = OutboundMessage.objects.filter(channel="email", to="ravi@example.com").first()
        self.assertIn(t.number, mail.body)
        self.assertIn("Hello Ravi,", mail.body)
        self.assertIn(f"http://diagtrack.local/tickets/{t.number}/", mail.body)
        self.assertNotIn("Machine:", mail.body)  # no machine on this ticket
        self.assertNotIn("{", mail.body)
        # P3 ticket: SMS and WhatsApp are for urgent alerts only by default
        self.assertFalse(OutboundMessage.objects.filter(channel__in=["sms", "whatsapp"]).exists())

    def test_saved_template_changes_wording_and_send_rule(self):
        self.client.force_login(self.admin)
        url = reverse("message_templates")
        r = self.client.post(url, {"event": "raised", "do": "save",
                                   "email-send": "always", "email-subject": "New: {ticket}", "email-body": "Hi {recipient}. {subject} at {centre}.",
                                   "sms-send": "always", "sms-body": "{ticket} {centre}", "sms-provider": "1107160000",
                                   "whatsapp-send": "never", "whatsapp-body": "x"})
        self.assertRedirects(r, url + "?event=raised")
        t = self.raise_ticket()
        mail = OutboundMessage.objects.get(channel="email", to="ravi@example.com")
        self.assertEqual(mail.subject, f"New: {t.number}")
        self.assertEqual(mail.body, "Hi Ravi. Printer not printing at Kukatpally.")
        sms = OutboundMessage.objects.filter(channel="sms").first()
        self.assertEqual((sms.body, sms.provider_template), (f"{t.number} Kukatpally", "1107160000"))
        self.assertFalse(OutboundMessage.objects.filter(channel="whatsapp").exists())
        # back to built-in
        self.client.post(url, {"event": "raised", "do": "reset"})
        from .models import MessageTemplate
        self.assertFalse(MessageTemplate.objects.filter(event="raised").exists())

    def test_template_screen_is_for_admins_and_warns_on_unknown_placeholder(self):
        self.client.force_login(self.it_head)
        self.assertEqual(self.client.get(reverse("message_templates")).status_code, 403)
        self.client.force_login(self.admin)
        page = self.client.get(reverse("message_templates") + "?event=escalation")
        self.assertContains(page, "Escalation")
        self.assertContains(page, "{<span>ticket</span>}")
        r = self.client.post(reverse("message_templates"), {
            "event": "closed", "do": "test", "email-send": "always", "email-subject": "s", "email-body": "{ticket} {tikcet}",
            "sms-send": "urgent", "sms-body": "{ticket}", "whatsapp-send": "urgent", "whatsapp-body": "{ticket}"}, follow=True)
        self.assertContains(r, "{tikcet} is not a placeholder")
        self.assertEqual(OutboundMessage.objects.filter(to__in=["boss@example.com", "919888888888"]).count(), 3)

    def test_gateway_template_placeholder(self):
        s = SiteSettings.load()
        s.sms_url, s.sms_body = "http://gw.example/send", '{"to": "{to}", "tpl": "{template}", "msg": "{message}"}'
        s.save()
        msg = OutboundMessage(channel="sms", to="91999", body='Say "hi"', provider_template="T1")
        with mock.patch("urllib.request.urlopen") as op:
            op.return_value.__enter__.return_value.status = 200
            notify.send_one(msg, s)
        sent = op.call_args[0][0].data.decode()
        self.assertEqual(sent, '{"to": "91999", "tpl": "T1", "msg": "Say \\"hi\\""}')

    def test_ticket_actions_are_one_line_buttons(self):
        t = self.raise_ticket()
        self.client.force_login(self.ravi)

        def bar():
            page = self.client.get(reverse("ticket_detail", args=[t.number])).content.decode()
            self.assertNotIn("data-menu", page[page.index('class="issue-side"'):page.index('class="side-card sla-card')])
            return page[page.index('class="actionbar"'):page.index("</div>", page.index('class="actionbar"'))]
        first = bar()
        for label in ("Start work", "Transfer", "Comment"):
            self.assertIn(label, first)
        self.assertIn("btn-primary", first.split("Start work")[0][-200:])
        self.client.post(reverse("ticket_action", args=[t.number, "start"]))
        second = bar()
        for label in ("Put on hold", "Resolve", "Transfer"):
            self.assertIn(label, second)
        self.assertIn("btn-primary", second.split("Resolve")[0][-200:])


# ==========================================================================
# Backups, runtime settings, maintenance and read-only modes
# ==========================================================================
import io as _io
import os as _os
import shutil as _shutil
import tempfile as _tempfile
from pathlib import Path as _Path

from django.test import TransactionTestCase, override_settings

from . import crypto as _crypto
from . import state as _state
from .backup import container as _container
from .backup import destinations as _dests
from .backup import engine as _engine
from .backup.runner import Runner as _Runner


class ContainerTests(TestCase):
    def roundtrip(self, data, pp="pass phrase one"):
        buf = _io.BytesIO()
        w = _container.Writer(buf, pp, b"0123456789abcdef", {"kind": "test"})
        w.write(data)
        w.close()
        return buf.getvalue()

    def test_roundtrip_and_summary(self):
        data = _os.urandom(3 * _container.CHUNK + 123)
        raw = self.roundtrip(data)
        r = _container.Reader(_io.BytesIO(raw), "pass phrase one")
        self.assertEqual(r.read(), data)
        self.assertEqual(r.summary, {"kind": "test"})

    def test_wrong_passphrase_damage_and_truncation_are_refused(self):
        raw = self.roundtrip(b"x" * 5000)
        with self.assertRaises(_container.BadPassphrase):
            _container.Reader(_io.BytesIO(raw), "wrong").read()
        bad = bytearray(raw)
        bad[-5] ^= 1
        with self.assertRaises((_container.Damaged, _container.BadPassphrase)):
            _container.Reader(_io.BytesIO(bytes(bad)), "pass phrase one").read()
        with self.assertRaises(_container.Damaged):
            _container.Reader(_io.BytesIO(raw[:-10]), "pass phrase one").read()


class CryptoAndRuntimeTests(TestCase):
    def test_field_encryption_and_legacy_plain_text(self):
        token = _crypto.encrypt("s3cret")
        self.assertTrue(token.startswith("enc1:"))
        self.assertEqual(_crypto.decrypt(token), "s3cret")
        self.assertEqual(_crypto.decrypt("plain old value"), "plain old value")
        s = SiteSettings.load()
        s.smtp_password = "mail-pass"
        s.save()
        from django.db import connection as _c
        with _c.cursor() as cur:
            cur.execute("SELECT smtp_password FROM tickets_sitesettings")
            self.assertTrue(cur.fetchone()[0].startswith("enc1:"))
        self.assertEqual(SiteSettings.load().smtp_password, "mail-pass")

    def test_settings_file_and_secrets_file(self):
        from config import runtime
        home = _Path(_tempfile.mkdtemp())
        try:
            runtime.write_conf(home / "diagtrack.conf", {"DT_TEST_PORT": "8181"})
            runtime.save_secrets(home / "secrets.dat", {"DT_TEST_SECRET": "abc"})
            with mock.patch.dict(_os.environ, {"DIAGTRACK_HOME": str(home)}):
                for key in ("DT_TEST_PORT", "MEDIA_ROOT", "STATUS_DIR"):
                    _os.environ.pop(key, None)
                runtime.load()
                self.assertEqual(_os.environ["DT_TEST_PORT"], "8181")
                self.assertEqual(_os.environ["DT_TEST_SECRET"], "abc")
                self.assertEqual(_os.environ["MEDIA_ROOT"], str(home / "media"))
        finally:
            _shutil.rmtree(home)


class ScheduleTests(TestCase):
    def test_last_slot_and_retention(self):
        from datetime import datetime as _dt
        tz = timezone.get_current_timezone()
        now = timezone.make_aware(_dt(2026, 10, 14, 10, 0), tz)  # a Wednesday
        d = _dests.default_destination("/x")
        self.assertEqual(_dests.last_slot(dict(d, frequency="daily", time="01:30"), now).hour, 1)
        self.assertEqual(_dests.last_slot(dict(d, frequency="daily", time="23:00"), now).day, 13)
        self.assertEqual(_dests.last_slot(dict(d, frequency="weekly", weekday=6, time="02:00"), now).day, 11)
        self.assertEqual(_dests.last_slot(dict(d, frequency="monthly", monthday=20, time="02:00"), now).month, 9)
        self.assertEqual(_dests.last_slot(dict(d, frequency="hours", every_hours=6, time="00:00"), now).hour, 6)
        entries = [{"name": f"b{i}", "created": now - timedelta(hours=12 * i)} for i in range(60)]
        keep = _dests.to_keep(entries, dict(d, keep_daily=3, keep_weekly=2, keep_monthly=1))
        self.assertIn("b0", keep)
        self.assertLessEqual(len(keep), 6)


@override_settings(STATUS_DIR=_Path(_tempfile.gettempdir()) / "dt-test-status")
class ModesTests(Base):
    def tearDown(self):
        _state.clear_maintenance()
        _state.clear_readonly()
        from . import middleware
        middleware._cache["at"] = 0

    def test_maintenance_page_and_readonly_blocks_changes(self):
        from . import middleware
        _state.set_maintenance("Restoring.")
        middleware._cache["at"] = 0
        r = self.client.get("/login/")
        self.assertEqual(r.status_code, 503)
        self.assertContains(r, "back shortly", status_code=503)
        self.assertEqual(self.client.get("/maintenance/status.json").json()["maintenance"], True)
        _state.clear_maintenance()
        _state.set_readonly("Moving.")
        middleware._cache["at"] = 0
        t = self.raise_ticket()
        self.client.force_login(self.ravi)
        self.client.post(reverse("ticket_action", args=[t.number, "comment"]), {"body": "hello"})
        self.assertEqual(t.comments.count(), 0)
        self.assertContains(self.client.get(reverse("home")), "Moving.")


class BackupBase(TransactionTestCase):
    """Real pg_dump / pg_restore against the test database."""

    def setUp(self):
        if "postgresql" not in settings.DATABASES["default"]["ENGINE"]:
            self.skipTest("needs PostgreSQL")
        self.tmp = _Path(_tempfile.mkdtemp())
        self.media = self.tmp / "media"
        (self.media / "tickets" / "KKP-000001").mkdir(parents=True)
        (self.media / "tickets" / "KKP-000001" / "photo.png").write_bytes(b"\x89PNG fake image" * 50)
        (self.media / "tickets" / "KKP-000001" / "report.pdf").write_bytes(b"%PDF fake" * 70)
        self.overrides = override_settings(MEDIA_ROOT=self.media, STATUS_DIR=self.tmp / "status")
        self.overrides.enable()
        self.env = mock.patch.dict(_os.environ, {"BACKUP_PASSPHRASE": "a long test passphrase"})
        self.env.start()
        call_command("setup_initial", verbosity=0)
        self.kkp = Centre.objects.create(code="KKP", name="Kukatpally")
        self.req = User.objects.create_user("meena", password="x" * 10, role=User.REQUESTER)
        self.req.centres.set([self.kkp])
        for i in range(3):
            services.create_ticket(self.req, self.kkp, Category.objects.get(name="Computer or printer"), f"T{i}", "d")
        self.dest = _dests.default_destination(str(self.tmp / "dest"))

    def tearDown(self):
        self.env.stop()
        self.overrides.disable()
        _shutil.rmtree(self.tmp, ignore_errors=True)
        name = settings.DATABASES["default"]["NAME"]
        try:
            with _engine.admin_connection() as conn:
                for suffix in ("_previous", "_incoming", "_verify"):
                    conn.execute(f'DROP DATABASE IF EXISTS "{name}{suffix}"')
        except Exception:
            pass


class BackupEngineTests(BackupBase):
    def test_backup_check_test_restore_and_restore(self):
        root = _dests.prepare(self.dest)
        out = _engine.create(root, kind="manual")
        self.assertTrue(out["check"]["ok"])
        self.assertEqual(out["counts"]["tickets"], 3)
        entries = _engine.catalog(root)
        self.assertEqual(entries[0]["summary"]["counts"]["tickets"], 3)
        self.assertEqual(len(list((root / "attachments").glob("*/*.dta"))), 2)
        self.assertEqual(_engine.test_restore(out["file"], root)["counts"]["tickets"], 3)

        Ticket.objects.first().delete()
        (self.media / "tickets" / "KKP-000001" / "photo.png").unlink()
        res = _engine.restore(out["file"], root)
        self.assertEqual(Ticket.objects.count(), 3)
        self.assertTrue((self.media / "tickets" / "KKP-000001" / "photo.png").exists())
        self.assertEqual(res["attachments_restored"], 1)

    def test_full_copy_restores_alone_and_secrets_follow_the_data(self):
        s = SiteSettings.load()
        s.smtp_password = "mail-secret"
        s.save()
        with mock.patch.dict(_os.environ, {"FIELD_KEY": _crypto.new_key_text()}):
            s.smtp_password = "mail-secret"
            s.save()
            out = _engine.create(kind="full", full_to=str(self.tmp / "usb"))
        _shutil.rmtree(self.media)
        with mock.patch.dict(_os.environ, {"FIELD_KEY": _crypto.new_key_text()}):  # "another server"
            _engine.restore(out["file"], None)
            self.assertEqual(SiteSettings.load().smtp_password, "mail-secret")
        self.assertEqual(len(list(self.media.rglob("*.*"))), 2)

    def test_wrong_passphrase_and_newer_version_are_refused(self):
        root = _dests.prepare(self.dest)
        out = _engine.create(root)
        with mock.patch.dict(_os.environ, {"BACKUP_PASSPHRASE": "not the one"}):
            with self.assertRaises(_engine.BackupError):
                _dests.prepare(self.dest) and _engine.store_salt(root)
            with self.assertRaises(_container.BadPassphrase):
                _engine.read_meta(out["file"])
        with self.assertRaises(_engine.BackupError):
            _engine.check_compatible({"schema": [["tickets", "9999_future"]], "app_version": "9.0"})

    def test_retention_keeps_newest_and_drops_unused_attachments(self):
        root = _dests.prepare(self.dest)
        first = _engine.create(root, kind="scheduled")
        (self.media / "tickets" / "KKP-000001" / "report.pdf").unlink()
        second = _engine.create(root, kind="scheduled")
        removed, dropped = _engine.apply_retention(root, dict(self.dest, keep_daily=1, keep_weekly=0, keep_monthly=0))
        self.assertEqual(removed, [_Path(first["file"]).name])
        self.assertEqual(dropped, 1)
        self.assertTrue(_Path(second["file"]).exists())

    def test_page_queues_jobs_and_runner_runs_them(self):
        admin = User.objects.create_user("boss", password="x" * 10, role=User.ADMIN, email="b@example.com")
        cfg = _dests.load_config()
        cfg["destinations"] = [self.dest]
        _dests.save_config(cfg)
        self.client.force_login(admin)
        self.assertEqual(self.client.get(reverse("backups")).status_code, 200)
        self.client.post(reverse("backups_action"), {"do": "backup"})
        job = _state.list_jobs(1)[0]
        self.assertEqual(job["status"], "queued")
        runner = _Runner()
        runner.tick()
        job = _state.get_job(job["id"])
        self.assertEqual(job["status"], "done", job.get("message"))
        page = self.client.get(reverse("backups"))
        self.assertContains(page, "Up to date")
        self.assertEqual(self.client.get(reverse("backup_job", args=[job["id"]])).json()["status"], "done")
        # restore needs the typed confirmation
        self.client.post(reverse("backups_action"), {"do": "restore", "dest": self.dest["id"], "name": "x", "confirm": "no"})
        self.assertEqual(_state.active_job(), None)
        # a failing destination alerts administrators once
        cfg["destinations"] = [dict(self.dest, path=str(self.tmp / "missing"), require_marker=True)]
        _dests.save_config(cfg)
        runner.backup_to(cfg["destinations"][0], "scheduled", lambda p, s: None)
        runner.backup_to(cfg["destinations"][0], "scheduled", lambda p, s: None)
        self.assertEqual(Notification.objects.filter(user=admin, kind="backupfail").count(), 1)

    def test_case_clash_is_renamed_on_windows(self):
        files = [{"path": "a/Photo.jpg", "sha256": "1" * 64, "size": 1}, {"path": "a/photo.jpg", "sha256": "2" * 64, "size": 1},
                 {"path": "../evil", "sha256": "3" * 64, "size": 1}]
        with mock.patch("tickets.backup.engine.sys.platform", "win32"):
            planned, renames = _engine.plan_targets(files)
        self.assertEqual(len(planned), 2)
        self.assertIn("a/photo.jpg", renames)
        self.assertEqual(len(_engine.name_clashes(files[:2])), 1)


class MoveTests(BackupBase):
    def test_prepare_to_move_end_move_and_retire(self):
        from . import middleware
        admin = User.objects.create_user("boss", password="x" * 10, role=User.ADMIN)
        self.client.force_login(admin)
        self.client.post(reverse("backups_action"), {"do": "moveout", "to": str(self.tmp / "move"), "confirm": "nope"})
        self.assertIsNone(_state.active_job())
        self.client.post(reverse("backups_action"), {"do": "moveout", "to": str(self.tmp / "move"), "confirm": "MOVE"})
        _Runner().tick()
        job = _state.list_jobs(1)[0]
        self.assertEqual(job["status"], "done", job.get("message"))
        self.assertTrue(_state.readonly())
        self.assertEqual(len(list((self.tmp / "move").glob("*-full.dtbak"))), 1)
        middleware._cache["at"] = 0
        self.assertContains(self.client.get(reverse("home")), "Moving to a new server")
        self.client.post(reverse("backups_action"), {"do": "endmove"})
        self.assertIsNone(_state.readonly())
        self.client.post(reverse("backups_action"), {"do": "retire", "confirm": "RETIRE"})
        self.assertTrue(_state.readonly())
        self.assertTrue(_state.read_json(_state.status_dir() / "retired.json"))

    def test_restore_job_applies_new_address(self):
        out = _engine.create(kind="full", full_to=str(self.tmp / "usb"))
        job = _state.new_job("restore", {"path": out["file"], "site_url": "http://new-server/"})
        _Runner().tick()
        self.assertEqual(_state.get_job(job["id"])["status"], "done")
        self.assertEqual(SiteSettings.load().site_url, "http://new-server")
        self.assertIsNone(_state.maintenance())
