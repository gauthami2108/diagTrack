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
        self.assertContains(resp, "brand-logo")
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


class SignInLockTests(Base):
    def login(self, name, password, addr="10.0.0.5", url="login"):
        return self.client.post(reverse(url) if url == "login" else url,
                                {"username": name, "password": password}, REMOTE_ADDR=addr)

    def setUp(self):
        super().setUp()
        self.boss = User.objects.create_user("boss", password="Boss-Pass-2026", role=User.ADMIN, is_superuser=True,
                                             is_staff=True)

    def test_five_wrong_passwords_pause_the_user_name_and_alert_administrators(self):
        for _ in range(4):
            r = self.login("ravi", "wrong")
            self.assertContains(r, "did not match")
        r = self.login("ravi", "wrong")
        self.assertContains(r, "Sign-in is paused until")
        # even the right password is refused while paused, from any computer
        r = self.login("ravi", "Demo@12345", addr="10.0.0.99")
        self.assertContains(r, "Sign-in is paused until")
        self.assertNotIn("_auth_user_id", self.client.session)
        self.assertEqual(Notification.objects.filter(user=self.boss, kind="signinlock").count(), 1)
        # other people are not affected
        self.login("meena", "Demo@12345")
        self.assertIn("_auth_user_id", self.client.session)

    def test_pause_ends_by_itself_and_success_clears_the_count(self):
        from .models import SignInLock
        for _ in range(5):
            self.login("ravi", "wrong")
        SignInLock.objects.filter(key="user:ravi").update(locked_until=timezone.now() - timedelta(seconds=1))
        self.login("ravi", "Demo@12345")
        self.assertIn("_auth_user_id", self.client.session)
        self.assertFalse(SignInLock.objects.filter(key="user:ravi").exists())

    def test_unknown_user_names_are_paused_too_so_names_cannot_be_probed(self):
        for _ in range(5):
            r = self.login("nobody-here", "wrong")
        self.assertContains(r, "Sign-in is paused until")

    def test_one_computer_trying_many_names_is_paused(self):
        with self.settings(LOGIN_MAX_FAILURES_PER_ADDRESS=6):
            for i in range(6):
                r = self.login(f"guess{i}", "wrong", addr="10.9.9.9")
            self.assertContains(r, "paused")
            r = self.login("meena", "Demo@12345", addr="10.9.9.9")
            self.assertContains(r, "from this computer")
            self.login("meena", "Demo@12345", addr="10.9.9.10")
            self.assertIn("_auth_user_id", self.client.session)

    def test_masters_and_rules_sign_in_is_protected_and_admin_can_unlock(self):
        for _ in range(5):
            r = self.login("boss", "wrong", url="/admin/login/")
        self.assertContains(r, "Sign-in is paused until")
        out = _io.StringIO()
        call_command("unlock", "boss", stdout=out)
        self.assertIn("unlocked", out.getvalue())
        self.login("boss", "Boss-Pass-2026", url="/admin/login/")
        self.assertIn("_auth_user_id", self.client.session)

    def test_unlock_action_on_users(self):
        for _ in range(5):
            self.login("ravi", "wrong")
        self.client.force_login(self.boss)
        page = self.client.get("/admin/tickets/user/").content.decode()
        self.assertIn("Paused until", page)
        self.client.post("/admin/tickets/user/", {"action": "unlock_sign_in", "_selected_action": [self.ravi.pk]})
        self.assertNotIn("Paused until", self.client.get("/admin/tickets/user/").content.decode())

    def test_address_comes_from_the_proxy_only_when_trusted(self):
        from django.test import RequestFactory
        from . import signin
        req = RequestFactory().get("/", REMOTE_ADDR="172.18.0.5", HTTP_X_FORWARDED_FOR="1.2.3.4, 192.168.1.40")
        with self.settings(TRUST_X_FORWARDED_FOR=False):
            self.assertEqual(signin.client_address(req), "172.18.0.5")
        with self.settings(TRUST_X_FORWARDED_FOR=True):
            self.assertEqual(signin.client_address(req), "192.168.1.40")

    def test_password_reset_link_lifts_the_pause(self):
        from django.contrib.auth.tokens import default_token_generator
        from django.utils.encoding import force_bytes
        from django.utils.http import urlsafe_base64_encode
        for _ in range(5):
            self.login("ravi", "wrong")
        uid = urlsafe_base64_encode(force_bytes(self.ravi.pk))
        url = reverse("password_reset_confirm", args=[uid, default_token_generator.make_token(self.ravi)])
        r = self.client.get(url, follow=True)
        self.client.post(r.redirect_chain[-1][0], {"new_password1": "Fresh-Pass-2026x", "new_password2": "Fresh-Pass-2026x"})
        self.login("ravi", "Fresh-Pass-2026x")
        self.assertIn("_auth_user_id", self.client.session)


from .models import Vendor  # noqa: E402


class BulkImportTests(Base):
    def book(self):
        from openpyxl import load_workbook
        from . import bulk_import
        return load_workbook(_io.BytesIO(bulk_import.workbook()))

    def save(self, wb):
        out = _io.BytesIO()
        wb.save(out)
        return out.getvalue()

    def add(self, wb, sheet, row):
        ws = wb[sheet]
        heads = [c.value.rstrip(" *") for c in ws[1]]
        ws.append([row.get(h, "") for h in heads])

    def test_downloaded_workbook_reads_back_unchanged(self):
        from . import bulk_import
        plan = bulk_import.read(self.save(self.book()))
        self.assertEqual(plan.errors, [])
        self.assertTrue(plan.empty)
        self.assertEqual(plan.counts("People")["same"], User.objects.count())

    def test_rows_may_refer_to_other_sheets_of_the_same_file(self):
        from . import bulk_import
        wb = self.book()
        self.add(wb, "Centres", {"Code": "MDP", "Name": "Madhapur", "City": "Hyderabad"})
        self.add(wb, "Vendors", {"Name": "Wipro GE Service", "Phone": 4012345678})
        self.add(wb, "Departments", {"Name": "Radiology", "Head (user name)": "lakshmi", "How tickets are assigned": "Least loaded",
                                     "Receives equipment tickets": "No"})
        self.add(wb, "People", {"User name": "lakshmi", "First name": "Lakshmi", "Role": "Department head",
                                "Centres": "MDP, KKP", "Departments": "Radiology", "Mobile": 919876543210.0})
        self.add(wb, "People", {"User name": "kumar", "First name": "Kumar", "Role": "Requester", "Centres": "MDP",
                                "Initial password": "Kumar-Pass-2026"})
        self.add(wb, "Equipment", {"Centre code": "MDP", "Name": "CT 64 slice", "Make": "GE", "Serial number": 77812,
                                   "Vendor": "Wipro GE Service", "AMC expiry date": "31-03-2027"})
        plan = bulk_import.read(self.save(wb))
        self.assertEqual([r.errors for r in plan.errors], [])
        counts, passwords = bulk_import.apply(plan)
        self.assertEqual(counts["People"]["new"], 2)
        lak = User.objects.get(username="lakshmi")
        self.assertEqual(lak.role, User.DEPT_HEAD)
        self.assertEqual(lak.mobile, "919876543210")
        self.assertEqual(sorted(c.code for c in lak.centres.all()), ["KKP", "MDP"])
        dept = Department.objects.get(name="Radiology")
        self.assertEqual((dept.head, dept.assignment_mode), (lak, Department.LEAST_LOADED))
        ct = Equipment.objects.get(serial_no="77812")
        self.assertEqual((ct.centre.code, ct.vendor.name, str(ct.amc_expiry)), ("MDP", "Wipro GE Service", "2027-03-31"))
        # a password was made for lakshmi only, and both can sign in
        self.assertEqual([u for u, _ in passwords], ["lakshmi"])
        self.assertTrue(self.client.login(username="lakshmi", password=passwords[0][1]))
        self.assertTrue(self.client.login(username="kumar", password="Kumar-Pass-2026"))

    def test_errors_are_listed_and_nothing_is_saved(self):
        from . import bulk_import
        wb = self.book()
        self.add(wb, "Centres", {"Code": "MDP", "Name": "Madhapur"})
        self.add(wb, "People", {"User name": "new one", "Role": "Boss"})
        self.add(wb, "People", {"User name": "x1", "Role": "Agent", "Centres": "ZZZ", "Departments": "Nowhere"})
        self.add(wb, "People", {"User name": "x1", "Role": "Agent"})
        self.add(wb, "Equipment", {"Centre code": "KKP", "Name": "Echo", "AMC expiry date": "next year"})
        plan = bulk_import.read(self.save(wb))
        text = " ".join(e for r in plan.errors for e in r.errors)
        for bit in ("User name:", "Role: use", "no centre with the code ZZZ", "no department called Nowhere",
                    "Same as row", "is not a date"):
            self.assertIn(bit, text)
        with self.assertRaises(ValueError):
            bulk_import.apply(plan)
        self.assertFalse(Centre.objects.filter(code="MDP").exists())

    def test_changes_only_what_is_filled_in(self):
        from . import bulk_import
        wb = self.book()
        ws = wb["People"]
        heads = [c.value.rstrip(" *") for c in ws[1]]
        for row in ws.iter_rows(min_row=2):
            if row[heads.index("User name")].value == "ravi":
                row[heads.index("Mobile")].value = 919800000001
                row[heads.index("Email")].value = None  # empty: left as it is
                row[heads.index("Active")].value = "No"
        self.ravi.email = "ravi@example.com"
        self.ravi.save()
        plan = bulk_import.read(self.save(wb))
        changed = [r for r in plan.rows["People"] if r.action == "change"]
        self.assertEqual([r.label for r in changed], ["ravi"])
        bulk_import.apply(plan)
        self.ravi.refresh_from_db()
        self.assertEqual((self.ravi.mobile, self.ravi.email, self.ravi.is_active), ("919800000001", "ravi@example.com", False))

    def test_adding_a_serial_number_updates_the_machine(self):
        from . import bulk_import
        self.mri.serial_no = ""
        self.mri.save()
        wb = self.book()
        ws = wb["Equipment"]
        heads = [c.value.rstrip(" *") for c in ws[1]]
        for row in ws.iter_rows(min_row=2):
            if row[heads.index("Name")].value == self.mri.name and row[heads.index("Centre code")].value == self.mri.centre.code:
                row[heads.index("Serial number")].value = "SN-1"
        before = Equipment.objects.count()
        bulk_import.apply(bulk_import.read(self.save(wb)))
        self.assertEqual(Equipment.objects.count(), before)
        self.mri.refresh_from_db()
        self.assertEqual(self.mri.serial_no, "SN-1")

    def test_screen_checks_then_imports_and_is_for_administrators_only(self):
        from django.core.files.uploadedfile import SimpleUploadedFile
        wb = self.book()
        self.add(wb, "Vendors", {"Name": "Philips Healthcare"})
        data = self.save(wb)
        self.client.force_login(self.ravi)
        self.assertEqual(self.client.get(reverse("import_page")).status_code, 403)
        admin = User.objects.create_user("boss", password="x", role=User.ADMIN, is_superuser=True, is_staff=True)
        self.client.force_login(admin)
        self.assertEqual(self.client.get(reverse("import_workbook")).status_code, 200)
        r = self.client.post(reverse("import_page"), {"step": "check", "file": SimpleUploadedFile("people.xlsx", data)})
        self.assertContains(r, "Ready to import")
        self.assertFalse(Vendor.objects.filter(name="Philips Healthcare").exists())
        token = r.context["token"]
        r = self.client.post(reverse("import_page"), {"step": "import", "token": token})
        self.assertContains(r, "Imported")
        self.assertTrue(Vendor.objects.filter(name="Philips Healthcare").exists())
        r = self.client.post(reverse("import_page"), {"step": "import", "token": token}, follow=True)
        self.assertContains(r, "expired")
        r = self.client.post(reverse("import_page"), {"step": "check", "file": SimpleUploadedFile("x.xlsx", b"not excel")},
                             follow=True)
        self.assertContains(r, "not an Excel workbook")


class Phase1Tests(Base):
    """Checks for Phase 1 enhancements: duplicates, bulk actions, PWA, and session invalidation."""

    def test_duplicate_detection_finds_open_tickets(self):
        t1 = services.create_ticket(self.req, self.kkp, self.mri_cat, "MRI chiller warm", "Chiller temperature high", equipment=self.mri)
        self.client.force_login(self.req)
        # Search by equipment
        res = self.client.get(reverse("api_duplicates") + f"?centre={self.kkp.pk}&equipment={self.mri.pk}").json()
        self.assertEqual(len(res["duplicates"]), 1)
        self.assertEqual(res["duplicates"][0]["number"], t1.number)

        # Search by title words
        res2 = self.client.get(reverse("api_duplicates") + f"?centre={self.kkp.pk}&q=chiller").json()
        self.assertEqual(len(res2["duplicates"]), 1)
        self.assertEqual(res2["duplicates"][0]["number"], t1.number)

    def test_bulk_operations(self):
        t1 = self.raise_ticket()
        t2 = self.raise_ticket()
        self.client.force_login(self.it_head)
        # Bulk assign to me
        resp = self.client.post(reverse("ticket_bulk"), {
            "ticket_numbers": f"{t1.number},{t2.number}",
            "bulk_action": "assign_me"
        }, HTTP_X_REQUESTED_WITH="XMLHttpRequest")
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.json()["ok"])
        t1.refresh_from_db()
        t2.refresh_from_db()
        self.assertEqual(t1.assignee, self.it_head)
        self.assertEqual(t2.assignee, self.it_head)

        # Bulk change priority
        resp = self.client.post(reverse("ticket_bulk"), {
            "ticket_numbers": f"{t1.number},{t2.number}",
            "bulk_action": "priority_p2"
        }, HTTP_X_REQUESTED_WITH="XMLHttpRequest")
        self.assertEqual(resp.status_code, 200)
        t1.refresh_from_db()
        self.assertEqual(t1.priority, "P2")

    def test_service_worker_and_pwa_routes(self):
        resp = self.client.get(reverse("service_worker"))
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp["Content-Type"], "application/javascript")
        self.assertEqual(resp["Service-Worker-Allowed"], "/")

    def test_session_invalidation(self):
        from . import signin
        s = self.client.session
        s["_auth_user_id"] = str(self.req.pk)
        s.save()
        count = signin.invalidate_user_sessions(self.req)
        self.assertGreaterEqual(count, 1)


class Phase2EnhancementTests(Base):
    """Checks for Phase 2 enhancements: SLA evaluation, weekly reports, and ticket archiving."""

    def test_database_level_sla_breach_detection(self):
        t0 = timezone.now()
        with at(t0):
            t = self.raise_ticket()
        with at(t0 + timedelta(minutes=125)):
            services.run_sla_checks()
        t.refresh_from_db()
        self.assertTrue(t.response_breached)
        self.assertEqual(t.assignee, self.it_head)

    def test_weekly_management_summary_and_dispatch(self):
        from . import reports
        from .models import DowntimeLog
        t1 = self.raise_ticket(patient_impact=True)
        t1.response_breached = True
        t1.save()
        DowntimeLog.objects.create(equipment=self.mri, ticket=t1, start=timezone.now() - timedelta(hours=5), end=timezone.now())

        summary = reports.generate_weekly_management_summary()
        self.assertIn("MISSED DEADLINES", summary["body"])
        self.assertIn("EQUIPMENT DOWNTIME", summary["body"])
        self.assertIn("CURRENT OPEN BACKLOG BY CENTRE", summary["body"])
        self.assertGreaterEqual(summary["breached_count"], 1)

        self.director.email = "director@example.com"
        self.director.save()
        sent = reports.send_weekly_management_report(force=True)
        self.assertGreaterEqual(sent, 1)
        self.assertTrue(OutboundMessage.objects.filter(channel="email", to="director@example.com").exists())

    def test_ticket_archiving_command(self):
        from django.core.management import call_command
        t = self.raise_ticket()
        services.start_work(t, t.assignee)
        services.resolve(t, t.assignee, "Done")
        services.close(t, self.req)
        t.closed_at = timezone.now() - timedelta(days=400)
        t.save()

        self.assertIn(t.number, services.visible_tickets(self.req).values_list("number", flat=True))

        call_command("archive_tickets", months=12)
        t.refresh_from_db()
        self.assertTrue(t.is_archived)
        self.assertIsNotNone(t.archived_at)

        self.assertNotIn(t.number, services.visible_tickets(self.req).values_list("number", flat=True))
        self.assertIn(t.number, services.visible_tickets(self.req, include_archived=True).values_list("number", flat=True))


class Phase3Tests(Base):
    """Checks for Phase 3 enhancements: SSE stream and Prometheus metrics."""

    def test_sse_live_stream_endpoint(self):
        self.client.force_login(self.req)
        Notification.objects.create(user=self.req, title="Test Alert", body="Alert body", urgent=True)
        resp = self.client.get(reverse("api_live_stream") + "?test=1")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp["Content-Type"], "text/event-stream")
        content = b"".join(resp.streaming_content).decode()
        self.assertIn("event: alert", content)
        self.assertIn("Test Alert", content)

    def test_prometheus_metrics_endpoint(self):
        resp = self.client.get(reverse("metrics"))
        self.assertEqual(resp.status_code, 200)
        self.assertIn("text/plain", resp["Content-Type"])
        body = resp.content.decode()
        self.assertIn("diagtrack_tickets_by_status", body)
        self.assertIn("diagtrack_sla_breaches_total", body)
        self.assertIn("diagtrack_equipment_down_active", body)
        self.assertIn("diagtrack_worker_heartbeat_age_seconds", body)

    def test_telegram_alert_queue_and_delivery(self):
        s = SiteSettings.load()
        s.telegram_enabled = True
        s.telegram_bot_token = "123456:TEST_TOKEN"
        s.save()

        # Alert the area manager who watches all tickets
        self.am.telegram_chat_id = "987654321"
        self.am.save()

        t = self.raise_ticket(patient_impact=True)
        msg = OutboundMessage.objects.filter(channel="telegram", to="987654321").first()
        self.assertIsNotNone(msg)
        self.assertIn(t.number, msg.body)

    @mock.patch("tickets.notify._telegram_send")
    def test_profile_test_telegram_action(self, mock_send):
        s = SiteSettings.load()
        s.telegram_bot_token = "123456:TEST_TOKEN"
        s.save()

        self.client.force_login(self.req)
        resp = self.client.post(reverse("profile"), {
            "first_name": "Meena",
            "last_name": "R",
            "telegram_chat_id": "11223344",
            "test_telegram": "1",
        }, follow=True)
        self.assertEqual(resp.status_code, 200)
        self.req.refresh_from_db()
        self.assertEqual(self.req.telegram_chat_id, "11223344")
        mock_send.assert_called_once()
        self.assertEqual(mock_send.call_args[0][1], "11223344")

    def test_pm_schedule_creation_and_properties(self):
        from .models import PreventiveMaintenance
        today = timezone.localdate()
        pm = PreventiveMaintenance.objects.create(
            equipment=self.mri,
            title="Quarterly QA & Calibration",
            frequency_months=3,
            next_due_date=today + timedelta(days=5),
            advance_notice_days=7,
        )
        self.assertFalse(pm.is_overdue)
        self.assertEqual(pm.days_until_due, 5)

        pm.next_due_date = today - timedelta(days=2)
        pm.save()
        self.assertTrue(pm.is_overdue)

    def test_run_pm_checks_auto_ticket_generation_and_idempotency(self):
        from .models import PreventiveMaintenance, Priority
        today = timezone.localdate()
        pm = PreventiveMaintenance.objects.create(
            equipment=self.mri,
            title="Helium Level & RF Check",
            frequency_months=1,
            next_due_date=today + timedelta(days=3),
            advance_notice_days=7,
            assigned_to=self.agent1,
        )
        created = services.run_pm_checks(today=today)
        self.assertEqual(created, 1)

        pm.refresh_from_db()
        self.assertIsNotNone(pm.last_ticket)
        t = pm.last_ticket
        self.assertIn("PM: Helium Level & RF Check", t.subject)
        self.assertEqual(t.priority, Priority.P3)
        self.assertEqual(t.assignee, self.agent1)
        self.assertEqual(t.equipment, self.mri)
        self.assertEqual(t.pm_schedule, pm)

        created_again = services.run_pm_checks(today=today)
        self.assertEqual(created_again, 0)

    def test_resolve_ticket_advances_pm_schedule(self):
        from .models import PreventiveMaintenance
        today = timezone.localdate()
        pm = PreventiveMaintenance.objects.create(
            equipment=self.mri,
            title="Biannual Coil Servicing",
            frequency_months=6,
            next_due_date=today,
            advance_notice_days=7,
        )
        services.run_pm_checks(today=today)
        pm.refresh_from_db()
        t = pm.last_ticket
        self.assertIsNotNone(t)

        services.start_work(t, self.agent1)
        services.resolve(t, self.agent1, "Replaced helium seal and calibrated coils.")
        pm.refresh_from_db()

        self.assertEqual(pm.last_performed_at, today)
        self.assertGreater(pm.next_due_date, today)

    def test_camc_uptime_penalties_calculation(self):
        from .models import DowntimeLog
        from . import reports
        now = timezone.now()

        self.mri.uptime_guarantee_percent = 98.0
        self.mri.penalty_rate_per_day = 10000.00
        self.mri.save()

        t = self.raise_ticket(patient_impact=True, equipment=self.mri)
        DowntimeLog.objects.filter(ticket=t).delete()
        DowntimeLog.objects.create(
            equipment=self.mri,
            ticket=t,
            start=now - timedelta(hours=120),
            end=now
        )

        res = reports.camc_uptime_penalties(centres=[self.mri.centre], days=90, now=now)
        mri_row = next((r for r in res["rows"] if r["equipment"].pk == self.mri.pk), None)
        self.assertIsNotNone(mri_row)
        self.assertEqual(mri_row["guarantee_pct"], 98.0)
        self.assertEqual(mri_row["down_hours"], 120.0)
        self.assertEqual(mri_row["allowable_down_hours"], 43.2)
        self.assertEqual(mri_row["excess_down_hours"], 76.8)
        self.assertEqual(mri_row["excess_days"], 3.2)
        self.assertEqual(mri_row["penalty_amount"], 32000.00)
        self.assertEqual(mri_row["status"], "penalty")

    def test_masters_hub_view_and_permissions(self):
        # Requester cannot access masters
        self.client.force_login(self.req)
        resp = self.client.get(reverse("masters_hub"))
        self.assertEqual(resp.status_code, 403)

        # Admin can access masters hub
        self.client.force_login(self.admin)
        resp = self.client.get(reverse("masters_hub"))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Masters and rules")
        self.assertContains(resp, "Departments")

        # Test tabs
        for tab in ("departments", "categories", "sla", "escalation", "users", "vendors"):
            resp = self.client.get(f"{reverse('masters_hub')}?tab={tab}")
            self.assertEqual(resp.status_code, 200)

    def test_masters_actions(self):
        self.client.force_login(self.admin)

        # Add Department
        resp = self.client.post(reverse("masters_action", kwargs={"section": "departments"}), {
            "do": "add",
            "name": "Radiology Physics",
            "assignment_mode": "round_robin",
        }, follow=True)
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(Department.objects.filter(name="Radiology Physics").exists())

        # Add Category
        dept = Department.objects.get(name="Radiology Physics")
        resp = self.client.post(reverse("masters_action", kwargs={"section": "categories"}), {
            "do": "add",
            "name": "Dosimetry Calibration",
            "department": dept.pk,
            "default_priority": "P2",
        }, follow=True)
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(Category.objects.filter(name="Dosimetry Calibration", department=dept).exists())

        # Configure SLA
        resp = self.client.post(reverse("masters_action", kwargs={"section": "sla"}), {
            "do": "save",
            "department": dept.pk,
            "priority": "P2",
            "response_minutes": "25",
            "resolution_minutes": "360",
        }, follow=True)
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(SlaPolicy.objects.filter(department=dept, priority="P2", response_minutes=25).exists())

    def test_masters_hub_all_13_tabs_filters_and_sorting(self):
        self.client.force_login(self.admin)
        all_tabs = (
            "centres", "departments", "categories", "sla", "escalation", "users",
            "locks", "vendors", "equipment", "pm", "alerts", "history", "vendor_calls"
        )
        for tab in all_tabs:
            resp = self.client.get(f"{reverse('masters_hub')}?tab={tab}")
            self.assertEqual(resp.status_code, 200, f"Tab {tab} failed to render")

        # Test search & filters on users tab
        resp = self.client.get(f"{reverse('masters_hub')}?tab=users&q={self.req.username}&role=requester&status=active&sort=name")
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, self.req.first_name)

        # Test filters on equipment tab
        resp = self.client.get(f"{reverse('masters_hub')}?tab=equipment&q=MRI&status=active")
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "MRI 1.5T")

        # Non-admin access check
        self.client.force_login(self.req)
        self.assertEqual(self.client.get(reverse("masters_hub")).status_code, 403)

    def test_masters_edit_actions(self):
        from .models import Vendor, PreventiveMaintenance, SignInLock
        self.client.force_login(self.admin)

        # 1. Edit Escalation Rule
        rule = EscalationRule.objects.create(name="Pre-breach Alert", basis=EscalationRule.RESOLUTION, percent=75, active=True)
        resp = self.client.post(reverse("masters_action", kwargs={"section": "escalation"}), {
            "do": "edit", "id": rule.pk, "name": "Updated 80% Warning", "basis": EscalationRule.RESOLUTION,
            "percent": "80", "priorities": "P1,P2", "notify_assignee": "on", "notify_dept_head": "on",
            "notify_centre_manager": "on", "active": "on"
        }, follow=True)
        self.assertEqual(resp.status_code, 200)
        rule.refresh_from_db()
        self.assertEqual(rule.name, "Updated 80% Warning")
        self.assertEqual(rule.percent, 80)
        self.assertEqual(rule.priorities, "P1,P2")
        self.assertTrue(rule.notify_centre_manager)

        # 2. Edit User (details + password reset)
        target_user = User.objects.create_user("test_tech", password="OldPassword123", role=User.AGENT,
                                               first_name="Teja", email="old@example.com", mobile="9900000001")
        resp = self.client.post(reverse("masters_action", kwargs={"section": "users"}), {
            "do": "edit", "id": target_user.pk, "first_name": "Tejaswi", "last_name": "Rao",
            "role": User.DEPT_HEAD, "email": "teja.rao@example.com", "mobile": "9900000002",
            "telegram_chat_id": "12345678", "new_password": "NewSecretPassword@999",
            "centres": [self.kkp.pk], "departments": [self.bio.pk], "active": "on"
        }, follow=True)
        self.assertEqual(resp.status_code, 200)
        target_user.refresh_from_db()
        self.assertEqual(target_user.first_name, "Tejaswi")
        self.assertEqual(target_user.last_name, "Rao")
        self.assertEqual(target_user.role, User.DEPT_HEAD)
        self.assertEqual(target_user.email, "teja.rao@example.com")
        self.assertEqual(target_user.mobile, "9900000002")
        self.assertEqual(target_user.telegram_chat_id, "12345678")
        self.assertTrue(target_user.check_password("NewSecretPassword@999"), "Password should be updated")
        self.assertFalse(target_user.check_password("OldPassword123"), "Old password should no longer work")
        self.assertIn(self.kkp, target_user.centres.all())
        self.assertIn(self.bio, target_user.departments.all())

        # 3. Edit Vendor
        vendor = Vendor.objects.create(name="Wipro GE", contact_person="Ramesh", phone="9800000000")
        resp = self.client.post(reverse("masters_action", kwargs={"section": "vendors"}), {
            "do": "edit", "id": vendor.pk, "name": "GE HealthCare India", "contact_person": "Suresh Kumar",
            "phone": "9811111111", "email": "service@gehealthcare.com", "notes": "24x7 critical SLA"
        }, follow=True)
        self.assertEqual(resp.status_code, 200)
        vendor.refresh_from_db()
        self.assertEqual(vendor.name, "GE HealthCare India")
        self.assertEqual(vendor.contact_person, "Suresh Kumar")
        self.assertEqual(vendor.phone, "9811111111")
        self.assertEqual(vendor.email, "service@gehealthcare.com")

        # 4. Edit Equipment
        resp = self.client.post(reverse("masters_action", kwargs={"section": "equipment"}), {
            "do": "edit", "id": self.mri.pk, "centre": self.sec.pk, "name": "MRI 3.0T Signa",
            "make": "GE", "model": "Signa Pioneer", "serial_no": "MR-SN-9988", "vendor": vendor.pk,
            "amc_expiry": "2027-12-31", "uptime_guarantee_percent": "98.5", "penalty_rate_per_day": "15000",
            "annual_camc_cost": "750000", "active": "on"
        }, follow=True)
        self.assertEqual(resp.status_code, 200)
        self.mri.refresh_from_db()
        self.assertEqual(self.mri.centre, self.sec)
        self.assertEqual(self.mri.name, "MRI 3.0T Signa")
        self.assertEqual(self.mri.make, "GE")
        self.assertEqual(self.mri.serial_no, "MR-SN-9988")
        self.assertEqual(self.mri.vendor, vendor)
        self.assertEqual(self.mri.uptime_guarantee_percent, 98.5)
        self.assertEqual(self.mri.penalty_rate_per_day, 15000)

        # 5. Edit PM Schedule
        pm = PreventiveMaintenance.objects.create(equipment=self.mri, title="Quarterly Chiller Check",
                                                  frequency_months=3, next_due_date=timezone.localdate())
        resp = self.client.post(reverse("masters_action", kwargs={"section": "pm"}), {
            "do": "edit", "id": pm.pk, "equipment": self.mri.pk, "title": "Bi-Monthly Helium & Chiller Inspection",
            "frequency_months": "2", "next_due_date": "2026-12-15", "advance_notice_days": "14",
            "assigned_to": self.naveen.pk, "notes": "Follow GE service checklist", "active": "on"
        }, follow=True)
        self.assertEqual(resp.status_code, 200)
        pm.refresh_from_db()
        self.assertEqual(pm.title, "Bi-Monthly Helium & Chiller Inspection")
        self.assertEqual(pm.frequency_months, 2)
        self.assertEqual(pm.advance_notice_days, 14)
        self.assertEqual(pm.assigned_to, self.naveen)

        # 6. Lift Sign-in Lock
        lock = SignInLock.objects.create(key="user:lockeduser", failures=5,
                                         locked_until=timezone.now() + timedelta(minutes=15),
                                         last_address="192.168.1.100")
        self.assertTrue(SignInLock.objects.filter(key="user:lockeduser").exists())
        resp = self.client.post(reverse("masters_action", kwargs={"section": "locks"}),
                                {"do": "lift", "key": lock.key}, follow=True)
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(SignInLock.objects.filter(key="user:lockeduser").exists())

    def test_users_table_contact_column_displays_mobile_and_omits_telegram_and_email(self):
        user_with_mobile = User.objects.create_user("user_with_mob", password="Demo@12345", role=User.AGENT,
                                                    first_name="Ramesh", email="ramesh@hospital.org",
                                                    mobile="+91 9123456789", telegram_chat_id="99887766")
        self.client.force_login(self.admin)
        resp = self.client.get(f"{reverse('masters_hub')}?tab=users")
        self.assertEqual(resp.status_code, 200)

        content = resp.content.decode()
        # Mobile number must be rendered
        self.assertIn("+91 9123456789", content)
        # Telegram ID and green Telegram pill should NOT be rendered in the Contact column
        self.assertNotIn("Telegram: 99887766", content)

    def test_dynamic_copyright_footer_uses_configured_org_name_without_hardcoded_default(self):
        settings_obj = SiteSettings.load()
        settings_obj.org_name = "Global Diagnostic Imaging Ltd"
        settings_obj.save()

        # Check authenticated base template
        self.client.force_login(self.admin)
        resp = self.client.get(reverse("home"))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "&copy; 2026 Global Diagnostic Imaging Ltd. All Rights Reserved.")
        self.assertNotContains(resp, "Pramodini Medicare Ltd")

        # Check unauthenticated auth base template
        self.client.logout()
        resp = self.client.get(reverse("login"))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "&copy; 2026 Global Diagnostic Imaging Ltd. All Rights Reserved.")
        self.assertNotContains(resp, "Pramodini Medicare Ltd")



class CategoryWithoutDescriptionTests(Base):
    """A category can drop the free-text 'What happened?' box and rely on its own questions."""

    def setUp(self):
        self.billing = Category.objects.get(name="Billing software")
        self.bill_no = self.billing.fields.get(label="Bill Number")
        self.remarks = self.billing.fields.get(label="Remarks")
        self.client.force_login(self.req)

    def post(self, **extra):
        data = {"centre": self.kkp.pk, "category": self.billing.pk, "subject": "Bill Cancellation"}
        data.update(extra)
        return self.client.post(reverse("ticket_new"), data)

    def test_migration_set_up_billing_software(self):
        self.assertFalse(self.billing.asks_description)
        self.assertTrue(self.bill_no.required and self.remarks.required)
        self.assertEqual(self.remarks.kind, "longtext")  # Remarks gets the big box

    def test_ticket_raised_without_a_description(self):
        resp = self.post(**{f"field_{self.bill_no.pk}": "B-1001", f"field_{self.remarks.pk}": "Duplicate bill"})
        t = Ticket.objects.get()
        self.assertRedirects(resp, reverse("ticket_detail", args=[t.number]))
        self.assertEqual(t.subject, "Bill Cancellation")
        self.assertEqual(t.extra, {"Bill Number": "B-1001", "Remarks": "Duplicate bill"})
        self.assertEqual(t.description, "Bill Number: B-1001\nRemarks: Duplicate bill")

    def test_required_questions_are_still_enforced(self):
        resp = self.post(**{f"field_{self.remarks.pk}": "Duplicate bill"})
        self.assertContains(resp, "Fill in: Bill Number.")
        self.assertFalse(Ticket.objects.exists())

    def test_other_categories_still_need_a_description(self):
        resp = self.client.post(reverse("ticket_new"), {"centre": self.kkp.pk, "category": self.printer.pk,
                                                        "subject": "Printer"})
        self.assertContains(resp, "Describe what happened.")

    def test_form_tells_the_page_which_categories_hide_the_box(self):
        page = self.client.get(reverse("ticket_new"))
        self.assertContains(page, "data-describe")
        self.assertEqual(page.context["meta"][str(self.billing.pk)]["describe"], False)
        self.assertEqual(page.context["meta"][str(self.printer.pk)]["describe"], True)

    def test_detail_page_does_not_repeat_the_answers(self):
        self.post(**{f"field_{self.bill_no.pk}": "B-1001", f"field_{self.remarks.pk}": "Duplicate bill"})
        t = Ticket.objects.get()
        page = self.client.get(reverse("ticket_detail", args=[t.number]))
        self.assertContains(page, "B-1001", count=1)


class DashboardCancelledTests(Base):
    def test_cancelled_tickets_are_counted_and_linked(self):
        keep, drop = self.raise_ticket(), self.raise_ticket()
        services.cancel(drop, self.it_head, "Raised by mistake")
        self.client.force_login(self.it_head)
        page = self.client.get(reverse("dashboard"))
        self.assertEqual(page.context["d"]["cancelled"], 1)
        self.assertEqual(page.context["d"]["head"]["open"], 1)  # cancelled tickets are not "open"
        self.assertContains(page, "Cancelled in 30 days")
        link = reverse("ticket_list") + "?view=all&state=any&status=cancelled&closed_from="
        self.assertContains(page, link)
        # the link lists exactly the cancelled ticket
        lst = self.client.get(reverse("ticket_list"), {"view": "all", "state": "any", "status": "cancelled",
                                                      "closed_from": page.context["d"]["since"].isoformat()})
        self.assertEqual([t.number for t in lst.context["page"]], [drop.number])

    def test_old_cancellations_fall_outside_the_period(self):
        t = self.raise_ticket()
        services.cancel(t, self.it_head, "Duplicate")
        Ticket.objects.filter(pk=t.pk).update(closed_at=timezone.now() - timedelta(days=45))
        self.client.force_login(self.it_head)
        self.assertEqual(self.client.get(reverse("dashboard")).context["d"]["cancelled"], 0)
        self.assertEqual(self.client.get(reverse("dashboard"), {"days": "90"}).context["d"]["cancelled"], 1)


class BulkAssignToTests(Base):
    """Bulk 'Assign to': follows the same role rules as the Assign box on a single ticket."""

    def setUp(self):
        self.t1, self.t2 = self.raise_ticket(), self.raise_ticket()
        # IT auto-assigns by default; these tests are about tickets that are waiting for someone to pick them
        Ticket.objects.filter(pk__in=[self.t1.pk, self.t2.pk]).update(assignee=None, status=Status.NEW)
        self.t1.refresh_from_db(), self.t2.refresh_from_db()

    def bulk(self, who, tickets, person):
        self.client.force_login(who)
        return self.client.post(reverse("ticket_bulk"), {
            "bulk_action": "assign_to", "bulk_assignee": person.pk,
            "ticket_numbers": ",".join(t.number for t in tickets)}, follow=True)

    def test_department_head_assigns_unassigned_tickets(self):
        resp = self.bulk(self.it_head, [self.t1, self.t2], self.ravi)
        self.assertContains(resp, "Assigned 2 ticket(s) to Ravi.")
        for t in (self.t1, self.t2):
            t.refresh_from_db()
            self.assertEqual((t.assignee, t.status), (self.ravi, "assigned"))
        self.assertEqual(Notification.objects.filter(user=self.ravi, kind="assigned").count(), 2)

    def test_agent_who_is_not_head_cannot_assign_to_others(self):
        resp = self.bulk(self.farah, [self.t1], self.ravi)
        self.assertContains(resp, "Assigned 0 ticket(s)")
        self.t1.refresh_from_db()
        self.assertIsNone(self.t1.assignee_id)

    def test_person_from_another_department_is_refused(self):
        resp = self.bulk(self.it_head, [self.t1], self.naveen)  # Naveen is a Biomedical agent
        self.assertContains(resp, "does not cover")
        self.t1.refresh_from_db()
        self.assertIsNone(self.t1.assignee_id)

    def test_requester_cannot_be_chosen(self):
        resp = self.bulk(self.it_head, [self.t1], self.other)
        self.assertContains(resp, "Assigned 0 ticket(s)")
        self.t1.refresh_from_db()
        self.assertIsNone(self.t1.assignee_id)

    def test_tickets_that_already_have_an_owner_can_be_moved(self):
        services.assign(self.t1, self.it_head, self.farah)
        resp = self.bulk(self.it_head, [self.t1, self.t2], self.ravi)
        self.assertContains(resp, "Assigned 2 ticket(s) to Ravi.")
        self.assertContains(resp, "1 moved from another person")
        self.t1.refresh_from_db()
        self.assertEqual(self.t1.assignee, self.ravi)
        last = self.t1.history.filter(field="assignee").latest("id")
        self.assertEqual((last.old, last.new), ("Farah", "Ravi"))  # the previous owner is on record

    def test_moving_keeps_the_status_of_work_in_progress(self):
        services.assign(self.t1, self.it_head, self.farah)
        services.start_work(self.t1, self.farah)
        self.bulk(self.it_head, [self.t1], self.ravi)
        self.t1.refresh_from_db()
        self.assertEqual((self.t1.assignee, self.t1.status), (self.ravi, Status.IN_PROGRESS))

    def test_tickets_already_with_that_person_are_skipped(self):
        services.assign(self.t1, self.it_head, self.ravi)
        resp = self.bulk(self.it_head, [self.t1], self.ravi)
        self.assertContains(resp, "Assigned 0 ticket(s)")
        self.assertContains(resp, "1 are already assigned to Ravi")

    def test_agent_limited_to_other_centre_is_refused(self):
        limited = User.objects.create_user("lim", password="x", role=User.AGENT, first_name="Lim")
        limited.departments.set([self.it])
        limited.centres.set([self.sec])  # works Secunderabad only; the tickets are at Kukatpally
        resp = self.bulk(self.it_head, [self.t1], limited)
        self.assertContains(resp, "does not cover")

    def test_only_heads_see_the_option_and_people_are_offered_with_their_coverage(self):
        self.client.force_login(self.it_head)
        page = self.client.get(reverse("ticket_list"), {"view": "all"})
        self.assertContains(page, 'value="assign_to"')
        names = {p["name"] for p in page.context["assign_people"]}
        self.assertTrue({"Ravi", "Farah", "Srinivas"} <= names)
        self.assertNotIn("Naveen", names)  # Biomedical is not this head's department
        self.client.force_login(self.farah)
        page = self.client.get(reverse("ticket_list"), {"view": "all"})
        self.assertNotContains(page, 'value="assign_to"')

    def test_single_ticket_assign_also_checks_the_person(self):
        self.client.force_login(self.it_head)
        url = reverse("ticket_action", args=[self.t1.number, "assign"])
        self.client.post(url, {"assignee": self.other.pk}, follow=True)  # a requester, via a crafted request
        self.t1.refresh_from_db()
        self.assertIsNone(self.t1.assignee_id)
        self.client.post(url, {"assignee": self.ravi.pk}, follow=True)
        self.t1.refresh_from_db()
        self.assertEqual(self.t1.assignee, self.ravi)


# --------------------------------------------------------------------------
# Insurance register
# --------------------------------------------------------------------------
import shutil as _ins_shutil
import tempfile as _ins_tempfile
from datetime import date as _ins_date

from django.contrib.messages import get_messages
from django.core.files.uploadedfile import SimpleUploadedFile as _InsFile

from .models import InsuranceCoverType, InsuranceDocument, InsurancePolicy


def _pdf(name="policy.pdf"):
    return _InsFile(name, b"%PDF-1.4 test policy", content_type="application/pdf")


def _grant_insurance(t):
    """Insurance is only for people given access: view own centres for managers, view all for management."""
    for u, level in ((t.cm, "own"), (t.am, "own"), (t.director, "all")):
        User.objects.filter(pk=u.pk).update(insurance_access=level)
        u.insurance_access = level


class InsuranceTests(Base):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._media = _ins_tempfile.mkdtemp()
        cls._override = override_settings(MEDIA_ROOT=cls._media)
        cls._override.enable()

    @classmethod
    def tearDownClass(cls):
        cls._override.disable()
        _ins_shutil.rmtree(cls._media, ignore_errors=True)
        super().tearDownClass()

    def setUp(self):
        _grant_insurance(self)
        self.admin = User.objects.create_user("boss", password="Demo@12345", role=User.ADMIN, email="boss@example.com")
        self.fire = InsuranceCoverType.objects.get(name="Fire")
        self.today = timezone.localdate()

    def policy(self, centres=(), days=200, cover=None, **kw):
        p = InsurancePolicy.objects.create(
            cover_type=cover or self.fire, insurer="New India", policy_number=kw.pop("number", "P-1"),
            start_date=self.today - timedelta(days=365 - days), expiry_date=self.today + timedelta(days=days),
            sum_insured=5000000, premium=42000, **{k: v for k, v in kw.items() if k != "responsible"})
        p.centres.set(centres)
        if kw.get("responsible"):
            p.responsible.set(kw["responsible"])
        return p

    def post_new(self, **over):
        data = {"cover_type": self.fire.pk, "centres": [self.kkp.pk], "insurer": "New India", "policy_number": "FIRE-9",
                "start_date": "2026-04-01", "expiry_date": "2027-03-31", "sum_insured": "1000000", "premium": "9000",
                "files": _pdf()}
        data.update(over)
        return self.client.post(reverse("insurance_new"), data)

    # --- who can see it -------------------------------------------------
    def test_ordinary_staff_cannot_open_the_page(self):
        for u in (self.req, self.ravi, self.it_head):
            self.client.force_login(u)
            self.assertEqual(self.client.get(reverse("insurance_list")).status_code, 403)
        self.client.force_login(self.admin)
        self.assertEqual(self.client.get(reverse("insurance_list")).status_code, 200)

    def test_menu_item_only_for_those_who_can_open_it(self):
        self.client.force_login(self.cm)
        self.assertContains(self.client.get(reverse("home")), reverse("insurance_list"))
        self.client.force_login(self.req)
        self.assertNotContains(self.client.get(reverse("home")), reverse("insurance_list"))

    def test_managers_see_their_centres_and_all_centre_policies_but_never_the_money(self):
        mine = self.policy([self.kkp], number="MINE")
        both = self.policy([self.kkp, self.sec], number="BOTH")
        other = self.policy([self.sec], number="OTHER")
        head_office = self.policy([], number="ALL")
        self.client.force_login(self.cm)  # Imran manages Kukatpally only
        page = self.client.get(reverse("insurance_list"))
        numbers = {p.policy_number for p in page.context["rows"]}
        self.assertEqual(numbers, {"MINE", "BOTH", "ALL"})  # ALL covers every centre, so it is shown too
        self.assertNotContains(page, "5000000")
        self.assertNotContains(page, "Sum insured")
        self.assertEqual(self.client.get(reverse("insurance_detail", args=[other.pk])).status_code, 404)
        self.assertEqual(self.client.get(reverse("insurance_detail", args=[head_office.pk])).status_code, 200)
        self.assertNotContains(self.client.get(reverse("insurance_detail", args=[mine.pk])), "42000")
        self.assertEqual(self.client.get(reverse("insurance_detail", args=[both.pk])).status_code, 200)

    def test_management_sees_money_until_it_is_switched_off(self):
        p = self.policy([self.kkp])
        self.client.force_login(self.director)
        self.assertContains(self.client.get(reverse("insurance_detail", args=[p.pk])), "₹42000")
        s = SiteSettings.load()
        s.insurance_show_money = False
        s.save()
        self.assertNotContains(self.client.get(reverse("insurance_detail", args=[p.pk])), "₹42000")
        self.assertNotContains(self.client.get(reverse("insurance_list")), "Sum insured")

    def test_only_administrators_can_change_things(self):
        p = self.policy([self.kkp])
        for u in (self.cm, self.am, self.director):
            self.client.force_login(u)
            self.assertEqual(self.client.get(reverse("insurance_new")).status_code, 403)
            self.assertEqual(self.client.get(reverse("insurance_edit", args=[p.pk])).status_code, 403)
            self.assertEqual(self.client.post(reverse("insurance_delete", args=[p.pk])).status_code, 403)

    def test_a_policy_can_have_several_responsible_people(self):
        a = User.objects.create_user("anil", password="Demo@12345", role=User.AGENT)
        b = User.objects.create_user("bina", password="Demo@12345", role=User.AGENT)
        self.client.force_login(self.admin)
        self.assertEqual(self.post_new(responsible=[a.pk, b.pk]).status_code, 302)
        p = InsurancePolicy.objects.get()
        self.assertEqual({u.pk for u in p.responsible.all()}, {a.pk, b.pk})
        page = self.client.get(reverse("insurance_detail", args=[p.pk]))
        self.assertContains(page, "anil")
        self.assertContains(page, "bina")
        # renewing starts from the same people
        form = self.client.get(reverse("insurance_new"), {"renew": p.pk}).context["form"]
        self.assertEqual({u.pk for u in form.initial["responsible"]}, {a.pk, b.pk})

    def test_responsible_is_optional(self):
        self.client.force_login(self.admin)
        self.assertEqual(self.post_new().status_code, 302)
        self.assertEqual(InsurancePolicy.objects.get().responsible.count(), 0)

    # --- the add form ---------------------------------------------------
    def test_form_offers_tick_lists_select_all_and_plus_one_year(self):
        self.client.force_login(self.admin)
        page = self.client.get(reverse("insurance_new")).content.decode()
        for needle in ('type="checkbox" name="centres"', 'type="checkbox" name="equipment"', 'type="checkbox" name="responsible"',
                       'data-check-all="ins-centres"', 'data-check-all="ins-machines"', 'data-check-all="ins-people"',
                       "data-plus-year", f'data-centre="{self.kkp.pk}"', "MRI 1.5T · "):
            self.assertIn(needle, page)
        self.assertIn("id_premium_due", page)
        self.assertIn('name="covers"', page)

    def test_ticked_boxes_are_kept_when_the_form_comes_back_with_an_error(self):
        self.client.force_login(self.admin)
        r = self.post_new(centres=[self.sec.pk, self.kkp.pk], equipment=[self.mri.pk], expiry_date="2026-01-01")
        self.assertEqual(r.status_code, 200)  # expiry before start: not saved
        page = r.content.decode()
        self.assertRegex(page, rf'name="centres" value="{self.sec.pk}" checked')
        self.assertRegex(page, rf'name="equipment" value="{self.mri.pk}" checked')

    def test_covers_and_next_premium_due_are_saved_and_shown(self):
        self.client.force_login(self.admin)
        self.post_new(covers="Building and contents, MRI room", premium_due="2026-12-01")
        p = InsurancePolicy.objects.get()
        self.assertEqual((p.covers, p.premium_due), ("Building and contents, MRI room", _ins_date(2026, 12, 1)))
        page = self.client.get(reverse("insurance_detail", args=[p.pk]))
        self.assertContains(page, "Building and contents, MRI room")
        self.assertContains(page, "01 Dec 2026")
        self.assertContains(self.client.get(reverse("insurance_list")), "MRI room")
        self.assertContains(self.client.get(reverse("insurance_list"), {"q": "MRI room"}), "FIRE-9")

    def test_the_same_policy_cannot_be_added_twice(self):
        self.client.force_login(self.admin)
        self.assertEqual(self.post_new().status_code, 302)
        r = self.post_new(insurer="new india", policy_number="fire-9")  # same insurer, number and start date
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "already in the register")
        self.assertEqual(InsurancePolicy.objects.count(), 1)
        self.assertEqual(self.post_new(start_date="2027-04-01", expiry_date="2028-03-31").status_code, 302)  # next year is fine
        p = InsurancePolicy.objects.get(start_date=_ins_date(2026, 4, 1))  # editing a policy is not a duplicate of itself
        r = self.client.post(reverse("insurance_edit", args=[p.pk]), {
            "cover_type": self.fire.pk, "insurer": "New India", "policy_number": "FIRE-9", "start_date": "2026-04-01",
            "expiry_date": "2027-03-31", "centres": [self.kkp.pk], "notes": "ok"})
        self.assertEqual(r.status_code, 302)

    def test_unusual_dates_warn_but_do_not_block(self):
        self.client.force_login(self.admin)
        r = self.post_new(policy_number="LONG", start_date="2026-04-01", expiry_date="2031-03-31", premium_due="2032-01-01")
        self.assertEqual(r.status_code, 302)  # saved
        msgs = [str(m) for m in get_messages(r.wsgi_request)]
        self.assertTrue(any("years" in m for m in msgs), msgs)
        self.assertTrue(any("premium due date is outside" in m for m in msgs), msgs)
        r = self.post_new(policy_number="SHORT", start_date="2026-04-01", expiry_date="2026-04-10")
        self.assertTrue(any("only 10 days" in str(m) for m in get_messages(r.wsgi_request)))
        self.client.get(reverse("insurance_list"))  # the page shows (and clears) the messages so far
        r = self.post_new(policy_number="ORDINARY", start_date=str(timezone.localdate()), expiry_date=str(timezone.localdate() + timedelta(days=364)))
        self.assertEqual(r.status_code, 302)
        page = self.client.get(reverse("insurance_list")).content.decode()
        self.assertIn("Policy saved.", page)
        self.assertNotIn("toast warning", page)

    def test_one_year_later_handles_leap_day(self):
        from .views_insurance import _plus_year
        self.assertEqual(_plus_year(_ins_date(2026, 4, 1)), _ins_date(2027, 3, 31))
        self.assertEqual(_plus_year(_ins_date(2028, 2, 29)), _ins_date(2029, 2, 28))

    # --- documents --------------------------------------------------------
    def test_documents_open_in_the_app_with_new_tab_and_download_choices(self):
        self.client.force_login(self.admin)
        self.post_new()
        p = InsurancePolicy.objects.get()
        url = reverse("insurance_doc", args=[p.documents.get().pk])
        page = self.client.get(reverse("insurance_detail", args=[p.pk])).content.decode()
        self.assertIn("data-doc-view", page)
        self.assertIn('id="doc-viewer"', page)
        self.assertIn(f'href="{url}" target="_blank"', page)        # Open in new tab
        self.assertIn(f'href="{url}?download=1"', page)             # Download

    def test_a_pdf_can_be_shown_inside_the_app_but_not_by_other_sites(self):
        self.client.force_login(self.admin)
        self.post_new()
        url = reverse("insurance_doc", args=[InsurancePolicy.objects.get().documents.get().pk])
        r = self.client.get(url)
        self.assertEqual(r["X-Frame-Options"], "SAMEORIGIN")
        self.assertEqual(r["Content-Security-Policy"], "frame-ancestors 'self'")
        self.assertEqual(r["X-Content-Type-Options"], "nosniff")
        self.assertIn("inline", r["Content-Disposition"])
        d = self.client.get(url + "?download=1")
        self.assertIn("attachment", d["Content-Disposition"])
        self.assertIn("default-src 'none'", d["Content-Security-Policy"])  # a download keeps the strict rule

    # --- who has access -------------------------------------------------
    def test_no_access_means_no_page_menu_or_files_even_for_managers_and_management(self):
        p = self.policy([self.kkp])
        for u in (self.cm, self.am, self.director):
            User.objects.filter(pk=u.pk).update(insurance_access="")
        for u in (self.cm, self.am, self.director):
            self.client.force_login(u)
            self.assertEqual(self.client.get(reverse("insurance_list")).status_code, 403)
            self.assertEqual(self.client.get(reverse("insurance_detail", args=[p.pk])).status_code, 403)
            self.assertEqual(self.client.get(reverse("insurance_export")).status_code, 403)
            self.assertNotContains(self.client.get(reverse("home")), reverse("insurance_list"))

    def test_an_ordinary_user_can_be_given_access(self):
        p = self.policy([self.kkp])
        User.objects.filter(pk=self.ravi.pk).update(insurance_access="own")  # an agent
        self.client.force_login(self.ravi)
        self.assertEqual(self.client.get(reverse("insurance_list")).status_code, 200)
        self.assertContains(self.client.get(reverse("home")), reverse("insurance_list"))
        self.assertEqual(self.client.get(reverse("insurance_detail", args=[p.pk])).status_code, 404)  # not his centre

    def test_view_own_also_sees_policies_that_cover_every_centre(self):
        everywhere = self.policy([], number="ALL")
        other = self.policy([self.sec], number="SEC")
        mine = self.policy([self.kkp], number="KKP")
        self.client.force_login(self.cm)  # view own: Kukatpally
        page = self.client.get(reverse("insurance_list")).content.decode()
        self.assertIn("ALL", page)
        self.assertIn("KKP", page)
        self.assertNotIn("SEC", page.replace("SECUNDERABAD", ""))
        self.assertEqual(self.client.get(reverse("insurance_detail", args=[everywhere.pk])).status_code, 200)
        self.assertEqual(self.client.get(reverse("insurance_detail", args=[other.pk])).status_code, 404)
        self.assertEqual(self.client.get(reverse("insurance_list"), {"centre": "0"}).content.decode().count("ALL") > 0, True)

    # --- change history ---------------------------------------------------
    def history(self, p):
        return [e.text for e in p.log.all()]

    def test_adding_a_policy_is_logged_with_who_did_it(self):
        self.client.force_login(self.admin)
        self.post_new()
        p = InsurancePolicy.objects.get()
        e = p.log.get()
        self.assertEqual(e.actor, self.admin)
        self.assertIn("Policy added, with 1 document", e.text)
        page = self.client.get(reverse("insurance_detail", args=[p.pk]))
        self.assertContains(page, "Change history")
        self.assertContains(page, "Policy added, with 1 document")

    def test_edits_are_logged_field_by_field(self):
        self.client.force_login(self.admin)
        self.post_new()
        p = InsurancePolicy.objects.get()
        self.client.post(reverse("insurance_edit", args=[p.pk]), {
            "cover_type": self.fire.pk, "insurer": "New India", "policy_number": "FIRE-9", "start_date": "2026-04-01",
            "expiry_date": "2028-03-31", "centres": [self.sec.pk, self.kkp.pk], "sum_insured": "2000000", "premium": "9000",
            "responsible": [self.cm.pk], "notes": "changed"})
        text = " | ".join(self.history(p))
        self.assertIn("Expiry date: 31 Mar 2027 → 31 Mar 2028", text)
        self.assertIn("Centres: added Secunderabad", text)
        self.assertIn("Responsible: added", text)
        self.assertIn("Sum insured changed", text)   # recorded as changed, the amounts are never written
        self.assertIn("Notes changed", text)
        self.assertNotIn("2000000", text)
        self.assertNotIn("1000000", text)
        self.assertNotIn("Premium changed", text)    # the premium did not change

    def test_saving_without_changes_logs_nothing_new(self):
        self.client.force_login(self.admin)
        self.post_new()
        p = InsurancePolicy.objects.get()
        before = p.log.count()
        self.client.post(reverse("insurance_edit", args=[p.pk]), {
            "cover_type": self.fire.pk, "insurer": "New India", "policy_number": "FIRE-9", "start_date": "2026-04-01",
            "expiry_date": "2027-03-31", "centres": [self.kkp.pk], "sum_insured": "1000000", "premium": "9000"})
        self.assertEqual(p.log.count(), before)

    def test_documents_renewal_and_cancelling_are_logged(self):
        self.client.force_login(self.admin)
        self.post_new()
        p = InsurancePolicy.objects.get()
        self.client.post(reverse("insurance_doc_add", args=[p.pk]), {"files": _pdf("endorsement.pdf")})
        first = p.documents.order_by("pk").first()
        self.client.post(reverse("insurance_doc_delete", args=[first.pk]))
        text = " | ".join(self.history(p))
        self.assertIn("Added 1 document: endorsement.pdf", text)
        self.assertIn("Removed document policy.pdf", text)
        self.client.post(reverse("insurance_new"), {
            "renew": p.pk, "cover_type": self.fire.pk, "centres": [self.kkp.pk], "insurer": "New India",
            "policy_number": "FIRE-9", "start_date": "2027-04-01", "expiry_date": "2028-03-31", "files": _pdf("new.pdf")})
        new = InsurancePolicy.objects.get(renewed_from=p)
        self.assertIn("Renewed by policy FIRE-9", " | ".join(self.history(p)))
        self.assertIn("Added as the renewal of policy FIRE-9", " | ".join(self.history(new)))
        self.client.post(reverse("insurance_edit", args=[new.pk]), {
            "cover_type": self.fire.pk, "insurer": "New India", "policy_number": "FIRE-9", "start_date": "2027-04-01",
            "expiry_date": "2028-03-31", "centres": [self.kkp.pk], "cancelled": "on"})
        self.assertIn("Cancelled: No → Yes", " | ".join(self.history(new)))

    def test_everyone_who_may_open_the_policy_sees_its_history(self):
        p = self.policy([self.kkp])
        from . import insurance_log
        insurance_log.record(p, self.admin, "Expiry date: 01 Jan 2027 → 01 Jan 2028")
        self.client.force_login(self.cm)
        self.assertContains(self.client.get(reverse("insurance_detail", args=[p.pk])), "01 Jan 2028")

    # --- home card ----------------------------------------------------------
    def test_home_shows_what_needs_attention_to_people_with_access(self):
        today = timezone.localdate()
        self.policy([self.kkp], number="GONE", days=-5)
        self.policy([self.kkp], number="SOON", days=12)
        late = self.policy([self.kkp], number="PREM", days=200)
        late.premium_due = today - timedelta(days=2)
        late.save()
        self.policy([self.sec], number="OTHER", days=-9)  # not Imran's centre
        self.client.force_login(self.cm)  # view own: Kukatpally
        card = self.client.get(reverse("home")).context["insurance"]
        self.assertEqual((card["expired"], card["expiring"], card["premiums"]), (1, 1, 1))
        self.assertEqual([i["kind"] for i in card["items"]], ["premium", "expired", "expiring"])
        page = self.client.get(reverse("home")).content.decode()
        self.assertIn("Insurance", page)
        self.assertIn("overdue by 2 days", page)
        self.assertIn("Expired 5 days ago", page)
        self.assertIn("Ends in 12 days", page)
        self.assertNotIn("5000000", page)

    def test_home_card_is_absent_without_access_and_calm_when_all_is_well(self):
        self.client.force_login(self.ravi)  # an agent with no access
        self.assertIsNone(self.client.get(reverse("home")).context["insurance"])
        self.assertNotContains(self.client.get(reverse("home")), "Open register")
        self.client.force_login(self.admin)
        # no policies yet, so every centre is missing the expected covers: that is not "nothing"
        page = self.client.get(reverse("home"))
        self.assertGreater(page.context["insurance"]["gaps"], 0)
        self.assertNotContains(page, "Nothing needs attention.")
        InsuranceCoverType.objects.update(core=False)  # nothing is expected, so nothing is missing
        self.assertContains(self.client.get(reverse("home")), "Nothing needs attention.")

    def test_a_requester_with_access_sees_the_home_card(self):
        User.objects.filter(pk=self.req.pk).update(insurance_access="all")
        self.client.force_login(self.req)
        self.assertContains(self.client.get(reverse("home")), "Open register")

    # --- claims -------------------------------------------------------------
    def claim_data(self, **over):
        d = {"incident_date": str(self.today - timedelta(days=10)), "description": "Compressor failure in the MRI",
             "equipment": self.mri.pk, "claim_number": "CL-77", "claimed_on": str(self.today - timedelta(days=8)),
             "status": "filed", "amount_claimed": "250000", "notes": "Surveyor visit booked"}
        d.update(over)
        return d

    def test_a_claim_is_added_listed_and_logged(self):
        p = self.policy([self.kkp])
        self.client.force_login(self.admin)
        r = self.client.post(reverse("claim_new", args=[p.pk]), self.claim_data())
        self.assertEqual(r.status_code, 302)
        c = p.claims.get()
        self.assertEqual((c.status, c.claim_number, c.equipment, c.created_by), ("filed", "CL-77", self.mri, self.admin))
        detail = self.client.get(reverse("insurance_detail", args=[p.pk]))
        self.assertContains(detail, "Compressor failure in the MRI")
        self.assertContains(detail, "claimed ₹250000")
        self.assertTrue(any("Claim added: Compressor failure" in t for t in [e.text for e in p.log.all()]))
        tab = self.client.get(reverse("insurance_list"), {"tab": "claims"})
        self.assertContains(tab, "Compressor failure in the MRI")
        self.assertEqual(tab.context["open_claims"], 1)

    def test_updating_a_claim_logs_the_status_and_filters_work(self):
        p = self.policy([self.kkp])
        self.client.force_login(self.admin)
        self.client.post(reverse("claim_new", args=[p.pk]), self.claim_data())
        c = p.claims.get()
        self.client.post(reverse("claim_edit", args=[c.pk]), self.claim_data(status="settled", amount_settled="180000"))
        c.refresh_from_db()
        self.assertEqual((c.status, str(c.amount_settled)), ("settled", "180000.00"))
        text = " | ".join(e.text for e in p.log.all())
        self.assertIn("status Filed → Settled", text)
        self.assertIn("amounts changed", text)
        self.assertNotIn("180000", text)
        self.assertEqual(self.client.get(reverse("insurance_list"), {"tab": "claims"}).context["claims"], [])  # not open any more
        self.assertEqual(len(self.client.get(reverse("insurance_list"), {"tab": "claims", "cstatus": "settled"}).context["claims"]), 1)
        self.assertEqual(len(self.client.get(reverse("insurance_list"), {"tab": "claims", "cstatus": "all", "q": "MRI"}).context["claims"]), 1)
        self.assertEqual(len(self.client.get(reverse("insurance_list"), {"tab": "claims", "cstatus": "all", "q": "nothing like this"}).context["claims"]), 0)

    def test_claim_rules(self):
        p = self.policy([self.kkp])
        self.client.force_login(self.admin)
        future = self.client.post(reverse("claim_new", args=[p.pk]), self.claim_data(incident_date=str(self.today + timedelta(days=3))))
        self.assertEqual(future.status_code, 200)
        self.assertContains(future, "cannot be in the future")
        early = self.client.post(reverse("claim_new", args=[p.pk]), self.claim_data(claimed_on=str(self.today - timedelta(days=30))))
        self.assertContains(early, "before the incident")
        self.assertEqual(p.claims.count(), 0)
        # an incident outside the policy dates is saved, with a warning
        old = str(self.today - timedelta(days=500))
        r = self.client.post(reverse("claim_new", args=[p.pk]), self.claim_data(incident_date=old, claimed_on=""))
        self.assertEqual(r.status_code, 302)
        self.assertContains(self.client.get(reverse("insurance_detail", args=[p.pk])), "outside this policy")

    def test_claim_machines_come_from_the_policy(self):
        p = self.policy([self.kkp])
        p.equipment.set([self.mri])
        self.client.force_login(self.admin)
        form = self.client.get(reverse("claim_new", args=[p.pk])).context["form"]
        self.assertEqual(list(form.fields["equipment"].queryset), [self.mri])

    def test_who_may_add_see_and_remove_claims(self):
        p = self.policy([self.kkp])
        other = self.policy([self.sec], number="SEC")
        self.client.force_login(self.admin)
        self.client.post(reverse("claim_new", args=[p.pk]), self.claim_data())
        self.client.post(reverse("claim_new", args=[other.pk]), self.claim_data(description="Secunderabad flood", equipment=""))
        c = p.claims.get()
        # view own: sees only Kukatpally's claim, cannot add, edit or remove, and never the amounts
        self.client.force_login(self.cm)
        tab = self.client.get(reverse("insurance_list"), {"tab": "claims"})
        self.assertContains(tab, "Compressor failure")
        self.assertNotContains(tab, "Secunderabad flood")
        self.assertNotContains(tab, "250000")
        self.assertEqual(self.client.get(reverse("claim_new", args=[p.pk])).status_code, 403)
        self.assertEqual(self.client.get(reverse("claim_edit", args=[c.pk])).status_code, 403)
        self.assertEqual(self.client.post(reverse("claim_delete", args=[c.pk])).status_code, 403)
        self.assertNotContains(self.client.get(reverse("insurance_detail", args=[p.pk])), "Add a claim")
        self.assertNotContains(self.client.get(reverse("insurance_detail", args=[p.pk])), "250000")
        # manage: may add and edit anywhere but not remove
        User.objects.filter(pk=self.cm.pk).update(insurance_access="manage")
        self.assertEqual(self.client.get(reverse("claim_edit", args=[c.pk])).status_code, 200)
        self.assertEqual(self.client.post(reverse("claim_delete", args=[c.pk])).status_code, 403)
        # a view-own person cannot reach a claim of another centre by address
        User.objects.filter(pk=self.cm.pk).update(insurance_access="own")
        self.client.force_login(self.admin)
        sec_claim = other.claims.get()
        self.client.force_login(self.cm)
        self.assertEqual(self.client.get(reverse("claim_edit", args=[sec_claim.pk])).status_code, 403)

    def test_an_administrator_can_remove_a_claim_and_it_is_logged(self):
        p = self.policy([self.kkp])
        self.client.force_login(self.admin)
        self.client.post(reverse("claim_new", args=[p.pk]), self.claim_data())
        c = p.claims.get()
        self.assertEqual(self.client.post(reverse("claim_delete", args=[c.pk])).status_code, 302)
        self.assertEqual(p.claims.count(), 0)
        self.assertTrue(any("Claim removed" in e.text for e in p.log.all()))

    def test_claims_export_follows_the_money_rule(self):
        import io
        from openpyxl import load_workbook
        p = self.policy([self.kkp])
        self.client.force_login(self.admin)
        self.client.post(reverse("claim_new", args=[p.pk]), self.claim_data())
        ws = load_workbook(io.BytesIO(self.client.get(reverse("claims_export"), {"cstatus": "all"}).content)).active
        heads = [c.value for c in ws[1]]
        self.assertIn("Amount claimed", heads)
        self.assertEqual(ws.cell(2, heads.index("What happened") + 1).value, "Compressor failure in the MRI")
        s = SiteSettings.load()
        s.insurance_show_money = False
        s.save()
        ws = load_workbook(io.BytesIO(self.client.get(reverse("claims_export"), {"cstatus": "all"}).content)).active
        self.assertNotIn("Amount claimed", [c.value for c in ws[1]])

    def test_a_requester_given_access_sees_the_menu_item_and_the_page(self):
        p = self.policy([self.kkp])
        self.req.centres.add(self.kkp)
        User.objects.filter(pk=self.req.pk).update(insurance_access="manage")
        self.client.force_login(self.req)
        self.assertContains(self.client.get(reverse("home")), 'data-tooltip="Insurance"')
        self.assertEqual(self.client.get(reverse("insurance_list")).status_code, 200)
        self.assertEqual(self.client.get(reverse("insurance_detail", args=[p.pk])).status_code, 200)
        User.objects.filter(pk=self.req.pk).update(insurance_access="")
        self.assertNotContains(self.client.get(reverse("home")), 'data-tooltip="Insurance"')

    def test_view_all_sees_every_policy_but_cannot_change(self):
        p = self.policy([self.sec], number="SEC")
        self.client.force_login(self.director)  # view all
        self.assertEqual(self.client.get(reverse("insurance_detail", args=[p.pk])).status_code, 200)
        self.assertEqual(self.client.get(reverse("insurance_edit", args=[p.pk])).status_code, 403)
        self.assertNotContains(self.client.get(reverse("insurance_detail", args=[p.pk])), "Renew")

    def test_manage_can_add_edit_renew_and_add_documents_but_not_delete_or_change_types(self):
        User.objects.filter(pk=self.cm.pk).update(insurance_access="manage")
        self.client.force_login(self.cm)
        self.assertEqual(self.post_new(centres=[self.sec.pk]).status_code, 302)  # sees and adds beyond own centre
        p = InsurancePolicy.objects.get()
        self.assertEqual(self.client.get(reverse("insurance_edit", args=[p.pk])).status_code, 200)
        self.assertEqual(self.client.post(reverse("insurance_doc_add", args=[p.pk]), {"files": _pdf("x.pdf")}).status_code, 302)
        self.assertEqual(p.documents.count(), 2)
        self.assertEqual(self.client.post(reverse("insurance_delete", args=[p.pk])).status_code, 403)
        self.assertEqual(self.client.post(reverse("insurance_types_action"), {"action": "add", "name": "X"}).status_code, 403)
        self.assertNotContains(self.client.get(reverse("insurance_detail", args=[p.pk])), "Delete this policy")

    def test_the_money_follows_the_level(self):
        p = self.policy([self.kkp])
        for level, who, shown in (("own", self.cm, False), ("all", self.director, True),
                                  ("manage", self.cm, True), ("own", self.am, False)):
            User.objects.filter(pk=who.pk).update(insurance_access=level)
            self.client.force_login(who)
            page = self.client.get(reverse("insurance_detail", args=[p.pk]))
            self.assertEqual("₹42000" in page.content.decode(), shown, (level, who.username))

    def test_administrators_set_access_in_masters(self):
        self.client.force_login(self.admin)
        r = self.client.post(reverse("masters_action", kwargs={"section": "users"}), {"do": "edit", "id": self.ravi.pk,
                                                       "first_name": "Ravi", "role": User.AGENT,
                                                       "insurance_access": "manage"})
        self.ravi.refresh_from_db()
        self.assertEqual(self.ravi.insurance_access, "manage")
        self.client.post(reverse("masters_action", kwargs={"section": "users"}), {"do": "edit", "id": self.ravi.pk,
                                                   "first_name": "Ravi", "role": User.AGENT, "insurance_access": "bogus"})
        self.ravi.refresh_from_db()
        self.assertEqual(self.ravi.insurance_access, "manage")  # an unknown value changes nothing

    # --- adding ---------------------------------------------------------
    def test_a_policy_needs_a_document_and_dates(self):
        self.client.force_login(self.admin)
        r = self.post_new(files="")
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "required")
        self.assertFalse(InsurancePolicy.objects.exists())
        r = self.post_new(expiry_date="2026-03-01")  # before the start
        self.assertEqual(r.status_code, 200)
        self.assertFalse(InsurancePolicy.objects.exists())
        r = self.post_new(start_date="")
        self.assertFalse(InsurancePolicy.objects.exists())

    def test_saves_a_policy_for_several_centres(self):
        self.client.force_login(self.admin)
        r = self.post_new(centres=[self.kkp.pk, self.sec.pk])
        p = InsurancePolicy.objects.get()
        self.assertRedirects(r, reverse("insurance_detail", args=[p.pk]))
        self.assertEqual(set(p.centres.all()), {self.kkp, self.sec})
        self.assertEqual(p.documents.count(), 1)
        self.assertEqual(p.created_by, self.admin)

    def test_no_centres_means_all_centres(self):
        self.client.force_login(self.admin)
        self.post_new(centres=[])
        p = InsurancePolicy.objects.get()
        self.assertEqual(p.location, "All centres")

    def test_only_pdf_and_images_up_to_20_mb(self):
        self.client.force_login(self.admin)
        r = self.post_new(files=_InsFile("macro.docx", b"PK\x03\x04", content_type="application/zip"))
        self.assertContains(r, "only PDF, JPG, PNG or WebP")
        r = self.post_new(files=_InsFile("fake.pdf", b"<script>alert(1)</script>", content_type="application/pdf"))
        self.assertContains(r, "does not look like a real PDF")
        big = _InsFile("big.pdf", b"%PDF" + b"0" * (20 * 1024 * 1024 + 1), content_type="application/pdf")
        self.assertContains(self.post_new(files=big), "larger than 20 MB")
        png = _InsFile("scan.png", b"\x89PNG\r\n\x1a\n" + b"0" * 50, content_type="image/png")
        self.post_new(files=png)
        self.assertEqual(InsurancePolicy.objects.count(), 1)

    def test_money_fields_are_ignored_when_switched_off(self):
        s = SiteSettings.load()
        s.insurance_show_money = False
        s.save()
        self.client.force_login(self.admin)
        page = self.client.get(reverse("insurance_new"))
        self.assertNotContains(page, 'name="sum_insured"')
        self.post_new(sum_insured="999")
        self.assertIsNone(InsurancePolicy.objects.get().sum_insured)

    # --- documents ------------------------------------------------------
    def test_documents_are_served_only_to_people_who_may_see_the_policy(self):
        self.client.force_login(self.admin)
        self.post_new(centres=[self.sec.pk])
        doc = InsurancePolicy.objects.get().documents.get()
        url = reverse("insurance_doc", args=[doc.pk])
        self.assertEqual(self.client.get(url).status_code, 200)
        self.client.force_login(self.cm)  # Kukatpally manager, policy is Secunderabad
        self.assertEqual(self.client.get(url).status_code, 404)
        self.client.force_login(self.am)  # area manager covers both
        self.assertEqual(self.client.get(url).status_code, 200)
        self.client.force_login(self.req)
        self.assertEqual(self.client.get(url).status_code, 403)

    def test_the_last_document_cannot_be_removed(self):
        self.client.force_login(self.admin)
        self.post_new()
        p = InsurancePolicy.objects.get()
        d1 = p.documents.get()
        self.client.post(reverse("insurance_doc_delete", args=[d1.pk]))
        self.assertEqual(p.documents.count(), 1)
        self.client.post(reverse("insurance_doc_add", args=[p.pk]), {"files": _pdf("receipt.pdf")})
        self.assertEqual(p.documents.count(), 2)
        self.client.post(reverse("insurance_doc_delete", args=[d1.pk]))
        self.assertEqual(p.documents.count(), 1)

    # --- renew ----------------------------------------------------------
    def test_renew_keeps_the_old_policy_and_moves_the_dates_on(self):
        old = self.policy([self.kkp, self.sec], days=10, remind_days=45)
        self.client.force_login(self.admin)
        page = self.client.get(reverse("insurance_new"), {"renew": old.pk})
        self.assertEqual(page.context["form"].initial["start_date"], old.expiry_date + timedelta(days=1))
        r = self.client.post(reverse("insurance_new"), {
            "renew": old.pk, "cover_type": self.fire.pk, "centres": [self.kkp.pk, self.sec.pk], "insurer": "New India",
            "policy_number": "P-2", "start_date": str(old.expiry_date + timedelta(days=1)),
            "expiry_date": str(old.expiry_date + timedelta(days=365)), "files": _pdf()})
        new = InsurancePolicy.objects.get(policy_number="P-2")
        self.assertRedirects(r, reverse("insurance_detail", args=[new.pk]))
        old.refresh_from_db()
        self.assertTrue(old.renewed)
        self.assertEqual(new.renewed_from, old)
        self.assertEqual(old.status_on(self.today, 60), "renewed")
        self.assertFalse(old.current)

    def test_renewing_does_not_mark_the_old_one_when_the_new_one_is_invalid(self):
        old = self.policy([self.kkp])
        self.client.force_login(self.admin)
        self.client.post(reverse("insurance_new"), {"renew": old.pk, "cover_type": self.fire.pk, "insurer": "x",
                                                    "policy_number": "P-3", "start_date": "2027-01-01",
                                                    "expiry_date": "2027-12-31"})  # no document
        old.refresh_from_db()
        self.assertFalse(old.renewed)

    # --- list, status, filters ------------------------------------------
    def test_status_markers_and_filters(self):
        self.policy([self.kkp], days=200, number="OK")
        self.policy([self.kkp], days=20, number="SOON")
        self.policy([self.kkp], days=-5, number="LATE")
        done = self.policy([self.kkp], days=-40, number="OLD", renewed=True)
        gone = self.policy([self.kkp], days=100, number="GONE", cancelled=True)
        self.client.force_login(self.admin)
        page = self.client.get(reverse("insurance_list"))
        shown = {p.policy_number: p.status for p in page.context["rows"]}
        self.assertEqual(shown, {"OK": "active", "SOON": "expiring", "LATE": "expired"})
        self.assertEqual(page.context["expiring_count"], 1)
        self.assertEqual(page.context["expired_count"], 1)
        self.assertContains(page, "Expires in 20 days")
        self.assertContains(page, "Expired 5 days ago")
        rows = self.client.get(reverse("insurance_list"), {"status": "all"}).context["rows"]
        self.assertEqual(len(rows), 5)
        rows = self.client.get(reverse("insurance_list"), {"status": "renewed"}).context["rows"]
        self.assertEqual([p.policy_number for p in rows], ["OLD"])

    def test_sorted_by_soonest_expiry_and_centre_filter_includes_all_centre_policies(self):
        self.policy([self.sec], days=30, number="SEC")
        self.policy([self.kkp], days=90, number="KKP")
        self.policy([], days=10, number="ALL")
        self.client.force_login(self.admin)
        rows = self.client.get(reverse("insurance_list")).context["rows"]
        self.assertEqual([p.policy_number for p in rows], ["ALL", "SEC", "KKP"])
        rows = self.client.get(reverse("insurance_list"), {"centre": self.kkp.pk}).context["rows"]
        self.assertEqual({p.policy_number for p in rows}, {"ALL", "KKP"})
        rows = self.client.get(reverse("insurance_list"), {"centre": "0"}).context["rows"]
        self.assertEqual([p.policy_number for p in rows], ["ALL"])

    def test_the_policys_own_reminder_days_decide_expiring_soon(self):
        self.policy([self.kkp], days=40, number="OWN", remind_days=45)
        self.policy([self.kkp], days=40, number="DEFAULT")  # Settings default is 60, so also soon
        s = SiteSettings.load()
        s.insurance_alert_days = 30
        s.save()
        self.client.force_login(self.admin)
        shown = {p.policy_number: p.status for p in self.client.get(reverse("insurance_list")).context["rows"]}
        self.assertEqual(shown, {"OWN": "expiring", "DEFAULT": "active"})

    # --- export, types, delete ------------------------------------------
    def test_excel_export_respects_the_money_rule(self):
        import io
        from openpyxl import load_workbook
        self.policy([self.kkp, self.sec])
        self.client.force_login(self.director)
        ws = load_workbook(io.BytesIO(self.client.get(reverse("insurance_export")).content)).active
        header = [c.value for c in ws[1]]
        self.assertIn("Sum insured", header)
        self.assertEqual(ws.cell(2, 1).value, "Kukatpally, Secunderabad")
        self.client.force_login(self.cm)
        ws = load_workbook(io.BytesIO(self.client.get(reverse("insurance_export")).content)).active
        self.assertNotIn("Sum insured", [c.value for c in ws[1]])
        self.assertEqual(ws.max_row, 2)

    def test_cover_types_can_be_added_renamed_and_switched_off(self):
        self.client.force_login(self.admin)
        url = reverse("insurance_types_action")
        self.client.post(url, {"action": "add", "name": "Marine transit"})
        self.assertTrue(InsuranceCoverType.objects.filter(name="Marine transit").exists())
        self.client.post(url, {"action": "add", "name": "marine TRANSIT"})  # same name, other case
        self.assertEqual(InsuranceCoverType.objects.filter(name__iexact="marine transit").count(), 1)
        t = InsuranceCoverType.objects.get(name="Marine transit")
        self.client.post(url, {"action": "save", "pk": t.pk, "name": "Marine cargo"})  # unticked: off
        t.refresh_from_db()
        self.assertEqual((t.name, t.active), ("Marine cargo", False))
        self.assertNotIn(t, list(self.client.get(reverse("insurance_new")).context["form"].fields["cover_type"].queryset))
        self.client.force_login(self.cm)
        self.assertEqual(self.client.post(url, {"action": "add", "name": "X"}).status_code, 403)
        self.assertEqual(self.client.get(reverse("insurance_list"), {"tab": "types"}).status_code, 302)

    def test_default_cover_types_exist(self):
        names = set(InsuranceCoverType.objects.values_list("name", flat=True))
        self.assertTrue({"Fire", "Building", "Public liability"} <= names)

    def test_deleting_a_renewal_makes_the_earlier_policy_current_again(self):
        old = self.policy([self.kkp], days=-1, number="A")
        self.client.force_login(self.admin)
        self.client.post(reverse("insurance_new"), {
            "renew": old.pk, "cover_type": self.fire.pk, "insurer": "x", "policy_number": "B",
            "start_date": str(self.today), "expiry_date": str(self.today + timedelta(days=364)), "files": _pdf()})
        new = InsurancePolicy.objects.get(policy_number="B")
        self.client.post(reverse("insurance_delete", args=[new.pk]))
        old.refresh_from_db()
        self.assertFalse(old.renewed)
        self.assertFalse(InsurancePolicy.objects.filter(policy_number="B").exists())


class InsuranceReminderTests(Base):
    def setUp(self):
        _grant_insurance(self)
        self.admin = User.objects.create_user("boss", password="Demo@12345", role=User.ADMIN, email="boss@example.com")
        self.sec_cm = User.objects.create_user("sec_cm", password="Demo@12345", role=User.CENTRE_MANAGER,
                                               email="sec@example.com")
        self.sec_cm.centres.set([self.sec])
        self.accounts = User.objects.create_user("accounts", password="Demo@12345", role=User.AGENT,
                                                 email="acc@example.com")
        self.fire = InsuranceCoverType.objects.get(name="Fire")
        self.expiry = _ins_date(2027, 3, 31)

    def policy(self, centres=(), **kw):
        who = kw.pop("responsible", None)
        p = InsurancePolicy.objects.create(cover_type=self.fire, insurer="New India", policy_number="F-1",
                                           start_date=_ins_date(2026, 4, 1), expiry_date=kw.pop("expiry", self.expiry),
                                           **kw)
        p.centres.set(centres)
        if who:
            p.responsible.set(who if isinstance(who, (list, tuple)) else [who])
        return p

    def run_on(self, days_left):
        return services.insurance_checks(self.expiry - timedelta(days=days_left))

    def who(self):
        return set(Notification.objects.filter(kind="insurance").values_list("user__username", flat=True))

    def admins(self):
        return set(User.objects.filter(role=User.ADMIN).values_list("username", flat=True))

    def test_each_stage_goes_out_once_then_the_day_after(self):
        self.policy([self.kkp])
        self.assertEqual(self.run_on(90), 0)  # too early
        self.assertEqual(self.run_on(59), 1)  # first reminder, 60 days
        self.assertEqual(self.run_on(59), 0)  # not again
        self.assertEqual(self.run_on(45), 0)
        self.assertEqual(self.run_on(30), 1)
        self.assertEqual(self.run_on(7), 1)
        self.assertEqual(self.run_on(0), 1)
        self.assertEqual(self.run_on(-1), 1)  # the day after: has expired
        self.assertEqual(self.run_on(-2), 0)
        self.assertEqual(Notification.objects.filter(kind="insurance").values("title").distinct().count(), 5)
        self.assertTrue(Notification.objects.filter(kind="insurance", title__contains="has expired").exists())
        self.assertTrue(Notification.objects.filter(kind="insurance", title__contains="ends today").exists())

    def test_a_late_worker_sends_only_the_latest_stage(self):
        self.policy([self.kkp])
        self.assertEqual(self.run_on(5), 1)  # nothing before, so one alert for where it stands now
        self.assertEqual(self.run_on(4), 0)

    def test_goes_to_admins_and_people_with_insurance_access_for_that_centre(self):
        self.policy([self.kkp], responsible=self.accounts)
        self.run_on(20)
        # Imran and Kavitha may view their centres, the director views all. Secunderabad's manager and the
        # responsible agent have no Insurance access, so neither is told.
        self.assertEqual(self.who(), self.admins() | {"imran", "kavitha", "director"})

    def test_the_responsible_person_is_told_when_they_have_access(self):
        User.objects.filter(pk=self.accounts.pk).update(insurance_access="own")
        self.policy([self.kkp], responsible=self.accounts)
        self.run_on(20)
        self.assertIn("accounts", self.who())

    def test_every_responsible_person_with_access_is_told(self):
        other = User.objects.create_user("other", password="Demo@12345", role=User.AGENT)
        User.objects.filter(pk__in=[self.accounts.pk, other.pk]).update(insurance_access="own")
        noaccess = User.objects.create_user("noaccess", password="Demo@12345", role=User.AGENT)
        self.policy([], responsible=[self.accounts, other, noaccess])
        self.run_on(20)
        self.assertEqual(self.who(), self.admins() | {"director", "accounts", "other", "imran", "kavitha"})  # all-centre policy

    def premium_policy(self, due, **kw):
        p = self.policy([self.kkp], **kw)
        p.premium_due = due
        p.save()
        return p

    def premium_notes(self):
        return list(Notification.objects.filter(kind="insurance", title__startswith="Premium"))

    def test_premium_reminders_come_7_days_before_and_on_the_day_once_each(self):
        due = _ins_date(2027, 1, 15)
        p = self.premium_policy(due)
        services.insurance_checks(due - timedelta(days=10))
        self.assertEqual(self.premium_notes(), [])
        services.insurance_checks(due - timedelta(days=7))
        first = len(self.premium_notes())
        self.assertGreater(first, 0)
        self.assertIn("due in 7 days", self.premium_notes()[0].title)
        services.insurance_checks(due - timedelta(days=7))  # same day again: nothing new
        services.insurance_checks(due - timedelta(days=3))
        self.assertEqual(len(self.premium_notes()), first)
        services.insurance_checks(due)
        self.assertEqual(len(self.premium_notes()), first * 2)
        self.assertTrue(any("due today" in n.title for n in self.premium_notes()))
        services.insurance_checks(due + timedelta(days=1))  # the date was not moved: one overdue alert
        self.assertEqual(len(self.premium_notes()), first * 3)
        self.assertTrue(any("overdue" in n.title for n in self.premium_notes()))
        services.insurance_checks(due + timedelta(days=2))  # and then no more
        services.insurance_checks(due + timedelta(days=20))
        self.assertEqual(len(self.premium_notes()), first * 3)

    def test_the_premium_alert_goes_to_the_same_people_and_never_shows_the_amount(self):
        User.objects.filter(pk=self.accounts.pk).update(insurance_access="own")
        due = _ins_date(2027, 1, 15)
        self.premium_policy(due, responsible=self.accounts)
        services.insurance_checks(due)
        self.assertEqual({n.user.username for n in self.premium_notes()}, self.admins() | {"imran", "kavitha", "director", "accounts"})
        for n in self.premium_notes():
            self.assertNotIn("₹", n.title + n.body)

    def test_an_overdue_alert_is_sent_even_if_the_earlier_ones_were_missed_but_not_for_old_dates(self):
        due = _ins_date(2027, 1, 15)
        self.premium_policy(due)
        services.insurance_checks(due + timedelta(days=3))  # first look is 3 days late: tell them
        self.assertTrue(any("overdue" in n.title for n in self.premium_notes()))
        Notification.objects.all().delete()
        InsurancePolicy.objects.update(premium_alert_for=None, premium_alert_stage=0)
        services.insurance_checks(due + timedelta(days=60))  # recorded long after: not announced
        self.assertEqual(self.premium_notes(), [])

    def test_paying_and_moving_the_date_stops_the_overdue_alert(self):
        due = _ins_date(2027, 1, 15)
        p = self.premium_policy(due)
        services.insurance_checks(due)
        p.refresh_from_db()
        p.premium_due = _ins_date(2027, 4, 15)
        p.save()
        services.insurance_checks(due + timedelta(days=1))
        self.assertFalse(any("overdue" in n.title for n in self.premium_notes()))

    def test_changing_the_due_date_after_paying_starts_the_reminders_again(self):
        due = _ins_date(2027, 1, 15)
        p = self.premium_policy(due)
        services.insurance_checks(due)
        count = len(self.premium_notes())
        p.refresh_from_db()
        p.premium_due = _ins_date(2027, 4, 15)  # next instalment
        p.save()
        services.insurance_checks(_ins_date(2027, 4, 8))
        self.assertGreater(len(self.premium_notes()), count)

    def test_no_premium_alert_without_a_due_date_or_for_cancelled_policies(self):
        self.policy([self.kkp])
        services.insurance_checks(_ins_date(2027, 1, 15))
        self.assertEqual(self.premium_notes(), [])
        p = self.premium_policy(_ins_date(2027, 1, 15))
        p.cancelled = True
        p.save()
        services.insurance_checks(_ins_date(2027, 1, 15))
        self.assertEqual(self.premium_notes(), [])

    def test_view_own_only_for_centres_the_policy_covers(self):
        User.objects.filter(pk=self.sec_cm.pk).update(insurance_access="own")
        self.policy([self.kkp, self.sec])
        self.run_on(20)
        self.assertEqual(self.who(), self.admins() | {"imran", "kavitha", "director", "sec_cm"})

    def test_a_policy_for_all_centres_goes_to_everyone_with_access(self):
        User.objects.filter(pk=self.accounts.pk).update(insurance_access="manage")
        self.policy([], responsible=self.accounts)
        self.run_on(20)
        # view-own people (Imran, Kavitha) see policies that cover every centre, so they are told too
        self.assertEqual(self.who(), self.admins() | {"director", "accounts", "imran", "kavitha"})
        self.assertNotIn("sec_cm", self.who())  # no Insurance access

    def test_the_policys_own_first_reminder_replaces_the_settings_number(self):
        self.policy([self.kkp], remind_days=14)
        self.assertEqual(self.run_on(40), 0)  # Settings say 60, but this policy starts at 14
        self.assertEqual(self.run_on(30), 0)
        self.assertEqual(self.run_on(14), 1)
        self.assertEqual(self.run_on(7), 1)
        self.assertEqual(services.insurance_stages(14), [14, 7, 0, -1])
        self.assertEqual(services.insurance_stages(60), [60, 30, 7, 0, -1])

    def test_the_settings_number_is_the_first_reminder(self):
        s = SiteSettings.load()
        s.insurance_alert_days = 90
        s.save()
        self.policy([self.kkp])
        self.assertEqual(self.run_on(89), 1)

    def test_renewed_and_cancelled_policies_stay_quiet(self):
        self.policy([self.kkp], renewed=True)
        self.policy([self.kkp], cancelled=True)
        self.assertEqual(self.run_on(5), 0)

    def test_changing_the_expiry_date_starts_again(self):
        p = self.policy([self.kkp])
        self.assertEqual(self.run_on(5), 1)
        self.assertEqual(self.run_on(4), 0)
        p.expiry_date = self.expiry + timedelta(days=365)
        p.save()
        self.assertEqual(services.insurance_checks(p.expiry_date - timedelta(days=5)), 1)

    def test_a_policy_recorded_long_after_it_ended_is_not_announced(self):
        self.policy([self.kkp])
        self.assertEqual(self.run_on(-100), 0)
        self.assertEqual(self.run_on(-101), 0)
        self.assertFalse(Notification.objects.filter(kind="insurance").exists())

    def test_alert_links_to_the_policy_and_is_urgent_in_the_last_week(self):
        p = self.policy([self.kkp])
        self.run_on(20)
        self.run_on(5)
        early, late = Notification.objects.filter(kind="insurance", user=self.admin).order_by("pk")
        self.assertEqual(early.link, f"/insurance/{p.pk}/")
        self.assertFalse(early.urgent)
        self.assertTrue(late.urgent)
        self.assertIn("F-1", late.body)

    def test_email_goes_out_when_switched_on(self):
        s = SiteSettings.load()
        s.email_enabled = True
        s.save()
        self.policy([self.kkp])
        self.run_on(20)
        self.assertTrue(OutboundMessage.objects.filter(channel="email", to="imran@example.com",
                                                       subject__contains="Fire insurance").exists())

    def test_the_wording_screen_lists_the_new_alert(self):
        self.client.force_login(self.admin)
        page = self.client.get(reverse("message_templates"), {"event": "insurance"})
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, "Insurance ending")


class InsuranceGridTests(Base):
    def setUp(self):
        _grant_insurance(self)
        self.admin = User.objects.create_user("boss", password="Demo@12345", role=User.ADMIN, email="boss@example.com")
        self.types = {n: InsuranceCoverType.objects.get(name=n) for n in ("Fire", "Building", "Cyber")}
        self.today = timezone.localdate()

    def policy(self, cover, centres, days, **kw):
        p = InsurancePolicy.objects.create(
            cover_type=self.types[cover], insurer="New India", policy_number=kw.pop("number", f"{cover}-{days}"),
            start_date=self.today - timedelta(days=300), expiry_date=self.today + timedelta(days=days), **kw)
        p.centres.set(centres)
        return p

    def grid(self, user=None, **params):
        self.client.force_login(user or self.admin)
        page = self.client.get(reverse("insurance_list"), {"tab": "gaps", **params})
        self.assertEqual(page.status_code, 200)
        return page

    def cell(self, page, centre, cover):
        row = next(r for r in page.context["gap_rows"] if r["centre"] == centre)
        return next(c for c in row["cells"] if c["type"].name == cover)

    def test_columns_are_the_types_marked_expected_and_active(self):
        page = self.grid()
        names = {t.name for t in page.context["gap_types"]}
        self.assertEqual(names, {"Fire", "Building", "Equipment / machinery breakdown", "Contents / stock"})
        InsuranceCoverType.objects.filter(name="Contents / stock").update(active=False)
        InsuranceCoverType.objects.filter(name="Cyber").update(core=True)
        names = {t.name for t in self.grid().context["gap_types"]}
        self.assertEqual(names, {"Fire", "Building", "Equipment / machinery breakdown", "Cyber"})

    def test_covered_expiring_expired_and_missing(self):
        self.policy("Fire", [self.kkp], 200)
        self.policy("Building", [self.kkp], 20)
        self.policy("Fire", [self.sec], -10)
        page = self.grid()
        self.assertEqual(self.cell(page, self.kkp, "Fire")["state"], "active")
        self.assertEqual(self.cell(page, self.kkp, "Building")["state"], "expiring")
        self.assertEqual(self.cell(page, self.sec, "Fire")["state"], "expired")
        self.assertEqual(self.cell(page, self.sec, "Building")["state"], "none")
        self.assertContains(page, "Missing")

    def test_a_policy_for_all_centres_covers_every_centre(self):
        self.policy("Building", [], 200)
        page = self.grid()
        for c in (self.kkp, self.sec):
            cell = self.cell(page, c, "Building")
            self.assertEqual(cell["state"], "active")
            self.assertTrue(cell["everywhere"])

    def test_the_longest_running_policy_counts_and_old_ones_do_not(self):
        self.policy("Fire", [self.kkp], 30, number="OLD")
        self.policy("Fire", [self.kkp], 300, number="NEWER")
        self.policy("Fire", [self.kkp], 400, number="RENEWED", renewed=True)
        self.policy("Fire", [self.kkp], 500, number="CANCELLED", cancelled=True)
        self.assertEqual(self.cell(self.grid(), self.kkp, "Fire")["policy"].policy_number, "NEWER")

    def test_gap_count_is_on_the_tab_and_counts_missing_and_expired(self):
        n_centres, n_types = Centre.objects.filter(active=True).count(), 4
        self.client.force_login(self.admin)
        page = self.client.get(reverse("insurance_list"))
        self.assertEqual(page.context["gap_total"], n_centres * n_types)
        self.policy("Fire", [self.kkp], 200)
        self.policy("Fire", [self.sec], -3)
        page = self.client.get(reverse("insurance_list"))
        self.assertEqual(page.context["gap_total"], n_centres * n_types - 1)  # kkp fire covered; sec fire expired still a gap

    def test_managers_see_only_their_centres_and_no_links_to_policies_of_other_centres(self):
        self.policy("Fire", [self.kkp], 200)
        self.policy("Building", [], 200)  # all centres: counts as cover, and a manager may open it
        page = self.grid(self.cm)  # Imran: Kukatpally only
        self.assertEqual([r["centre"] for r in page.context["gap_rows"]], [self.kkp])
        self.assertTrue(self.cell(page, self.kkp, "Fire")["link"])
        building = self.cell(page, self.kkp, "Building")
        self.assertEqual(building["state"], "active")
        self.assertTrue(building["link"])
        self.assertNotContains(page, "Secunderabad")

    def test_requesters_cannot_open_the_grid(self):
        self.client.force_login(self.req)
        self.assertEqual(self.client.get(reverse("insurance_list"), {"tab": "gaps"}).status_code, 403)

    def test_cover_types_can_be_marked_expected(self):
        self.client.force_login(self.admin)
        url = reverse("insurance_types_action")
        self.client.post(url, {"action": "add", "name": "Directors liability", "core": "on"})
        t = InsuranceCoverType.objects.get(name="Directors liability")
        self.assertTrue(t.core)
        self.client.post(url, {"action": "save", "pk": t.pk, "name": t.name, "active": "on"})  # core unticked
        t.refresh_from_db()
        self.assertFalse(t.core)
        self.assertTrue(t.active)


class InsuranceLinkTests(Base):
    """Phase 4: the machine page panel, the unlinked machines column and the 'No document' marker."""

    def setUp(self):
        _grant_insurance(self)
        self.admin = User.objects.create_user("boss", password="Demo@12345", role=User.ADMIN, email="boss@example.com")
        self.eq_cover = InsuranceCoverType.objects.get(name="Equipment / machinery breakdown")
        self.today = timezone.localdate()

    def policy(self, centres, days, machines=(), **kw):
        p = InsurancePolicy.objects.create(cover_type=self.eq_cover, insurer="New India",
                                           policy_number=kw.pop("number", f"EQ-{days}"),
                                           start_date=self.today - timedelta(days=300),
                                           expiry_date=self.today + timedelta(days=days), **kw)
        p.centres.set(centres)
        p.equipment.set(machines)
        return p

    def machine_page(self, user):
        self.client.force_login(user)
        return self.client.get(reverse("equipment_detail", args=[self.mri.pk]))

    def test_the_machine_page_says_until_when_it_is_insured(self):
        p = self.policy([self.kkp], 200, [self.mri], sum_insured=7654321, premium=12345)
        page = self.machine_page(self.admin)
        self.assertContains(page, "Insured until")
        self.assertContains(page, f"{p.expiry_date:%d %b %Y}")
        self.assertContains(page, reverse("insurance_detail", args=[p.pk]))
        self.assertNotContains(page, "7654321")  # no money figures on the machine page
        self.assertNotContains(page, "12345")

    def test_expiring_and_ended_cover_is_flagged_and_old_policies_are_left_out(self):
        self.policy([self.kkp], 20, [self.mri], number="SOON")
        self.policy([self.kkp], -4, [self.mri], number="ENDED")
        self.policy([self.kkp], 300, [self.mri], number="GONE", cancelled=True)
        self.policy([self.kkp], 300, [self.mri], number="DONE", renewed=True)
        page = self.machine_page(self.admin)
        self.assertContains(page, "20 days left")
        self.assertContains(page, "Insurance ended")
        self.assertEqual({p.policy_number for p in page.context["insurance"]}, {"SOON", "ENDED"})

    def test_no_policy_says_so(self):
        self.assertContains(self.machine_page(self.admin), "No insurance policy lists this machine")

    def test_only_people_who_may_see_insurance_get_the_panel(self):
        self.policy([self.kkp], 200, [self.mri])
        self.policy([self.sec], 200, [self.mri], number="OTHER-CENTRE")
        self.assertIsNotNone(self.machine_page(self.cm).context["insurance"])
        self.assertEqual({p.policy_number for p in self.machine_page(self.cm).context["insurance"]}, {"EQ-200"})
        self.assertIsNone(self.machine_page(self.ravi).context["insurance"])  # an agent
        self.assertNotContains(self.machine_page(self.ravi), "Insured until")

    def test_grid_counts_machines_not_linked_to_a_current_policy(self):
        self.client.force_login(self.admin)

        def row():
            page = self.client.get(reverse("insurance_list"), {"tab": "gaps"})
            return next(r for r in page.context["gap_rows"] if r["centre"] == self.kkp)

        total = row()["machines"]
        self.assertGreaterEqual(total, 1)
        self.assertEqual(row()["unlinked"], total)
        p = self.policy([self.kkp], 200, [self.mri])
        self.assertEqual(row()["unlinked"], total - 1)
        p.expiry_date = self.today - timedelta(days=1)
        p.save()
        self.assertEqual(row()["unlinked"], total)  # an ended policy no longer counts

    def test_policies_without_a_document_are_marked(self):
        self.policy([self.kkp], 200, number="BARE")
        with_doc = self.policy([self.kkp], 100, number="HASDOC")
        InsuranceDocument.objects.create(policy=with_doc, file="insurance/x.pdf", name="x.pdf", size=1)
        self.client.force_login(self.admin)
        page = self.client.get(reverse("insurance_list"))
        self.assertEqual({p.policy_number: p.doc_count for p in page.context["rows"]}, {"BARE": 0, "HASDOC": 1})
        self.assertContains(page, "No document", count=1)


class InsuranceImportTests(Base):
    """Phase 4: the Insurance sheet of the Excel import."""

    def setUp(self):
        _grant_insurance(self)
        self.fire = InsuranceCoverType.objects.get(name="Fire")
        self.building = InsuranceCoverType.objects.get(name="Building")

    def book(self):
        from openpyxl import load_workbook
        from . import bulk_import
        return load_workbook(_io.BytesIO(bulk_import.workbook()))

    def save(self, wb):
        out = _io.BytesIO()
        wb.save(out)
        return out.getvalue()

    def add(self, wb, row, sheet="Insurance"):
        ws = wb[sheet]
        heads = [c.value.rstrip(" *") for c in ws[1]]
        ws.append([row.get(h, "") for h in heads])

    def row(self, **over):
        base = {"Cover type": "Fire", "Centres": "KKP", "Insurer": "New India", "Policy number": "F-100",
                "Start date": "01-04-2026", "Expiry date": "31-03-2027"}
        base.update(over)
        return base

    def read(self, wb):
        from . import bulk_import
        return bulk_import.read(self.save(wb))

    def errors(self, plan):
        return [e for r in plan.errors for e in r.errors]

    def test_the_workbook_has_the_sheet_and_reads_back_unchanged(self):
        from . import bulk_import
        boss = User.objects.create_user("accts", password="Demo@12345", role=User.AGENT)
        a = InsurancePolicy.objects.create(cover_type=self.fire, insurer="New India", policy_number="0012/34",
                                           start_date=_ins_date(2026, 4, 1), expiry_date=_ins_date(2027, 3, 31),
                                           sum_insured=5000000, premium=42500.5, remind_days=45,
                                           contact="Ravi 9876543210")
        a.centres.set([self.kkp, self.sec])
        a.responsible.set([boss])
        b = InsurancePolicy.objects.create(cover_type=self.building, insurer="Oriental", policy_number="B-9",
                                           start_date=_ins_date(2026, 1, 1), expiry_date=_ins_date(2026, 12, 31),
                                           renewed=True)
        self.assertIn("Insurance", bulk_import.ORDER)
        wb = self.book()
        self.assertIn("Insurance", wb.sheetnames)
        plan = self.read(wb)
        self.assertEqual(plan.errors, [])
        self.assertTrue(plan.empty)
        self.assertEqual(plan.counts("Insurance")["same"], 2)

    def test_people_sheet_sets_insurance_access_and_exports_it(self):
        from . import bulk_import
        wb = self.book()
        self.add(wb, {"User name": "meera", "Role": "Agent", "Insurance access": "View all centres"}, "People")
        ws = wb["People"]  # the workbook already lists everyone: change Ravi's cell
        heads = [c.value.rstrip(" *") for c in ws[1]]
        for row in ws.iter_rows(min_row=2):
            if row[heads.index("User name")].value == "ravi":
                row[heads.index("Insurance access")].value = "Manage"
        plan = self.read(wb)
        self.assertEqual(self.errors(plan), [])
        bulk_import.apply(plan)
        self.assertEqual(User.objects.get(username="meera").insurance_access, "all")
        self.assertEqual(User.objects.get(username="ravi").insurance_access, "manage")
        rows = {r["username"]: r["insurance_access"] for r in bulk_import._current_rows()["People"]}
        self.assertEqual(rows["ravi"], "Manage")
        self.assertEqual(rows["imran"], "View own centres")
        bad = self.book()
        self.add(bad, {"User name": "zed", "Role": "Agent", "Insurance access": "Everything"}, "People")
        self.assertIn("Insurance access", " ".join(self.errors(self.read(bad))))

    def test_covers_and_next_premium_due_come_in_and_go_out(self):
        from . import bulk_import
        wb = self.book()
        self.add(wb, self.row(**{"Covers": "Building and contents", "Next premium due": "15-01-2027"}))
        plan = self.read(wb)
        self.assertEqual(self.errors(plan), [])
        bulk_import.apply(plan)
        p = InsurancePolicy.objects.get()
        self.assertEqual((p.covers, p.premium_due), ("Building and contents", _ins_date(2027, 1, 15)))
        row = bulk_import._current_rows()["Insurance"][0]
        self.assertEqual((row["covers"], row["premium_due"]), ("Building and contents", _ins_date(2027, 1, 15)))
        self.assertEqual([r.action for r in self.read(self.book()).rows["Insurance"]], ["same"])
        bad = self.book()
        self.add(bad, self.row(**{"Policy number": "Z", "Next premium due": "soon"}))
        self.assertIn("Next premium due", " ".join(self.errors(self.read(bad))))

    def test_the_import_writes_history_too(self):
        from . import bulk_import
        wb = self.book()
        self.add(wb, self.row())
        bulk_import.apply(self.read(wb))
        p = InsurancePolicy.objects.get()
        self.assertEqual([e.text for e in p.log.all()], ["Policy added from the Excel import"])
        wb = self.book()
        ws = wb["Insurance"]
        heads = [c.value.rstrip(" *") for c in ws[1]]
        ws.cell(2, heads.index("Covers") + 1).value = "Building"
        bulk_import.apply(self.read(wb))
        self.assertIn("Excel import. Covers: none → Building", [e.text for e in p.log.all()])

    def test_several_responsible_people_are_read_changed_and_exported(self):
        from . import bulk_import
        for n in ("anil", "bina"):
            User.objects.create_user(n, password="Demo@12345", role=User.AGENT)
        wb = self.book()
        self.add(wb, self.row(**{"Responsible (user names)": "anil, bina"}))
        plan = self.read(wb)
        self.assertEqual(self.errors(plan), [])
        bulk_import.apply(plan)
        p = InsurancePolicy.objects.get()
        self.assertEqual({u.username for u in p.responsible.all()}, {"anil", "bina"})
        rows = bulk_import._current_rows()["Insurance"]
        self.assertEqual(sorted(x.strip() for x in rows[0]["responsible"].split(",")), ["anil", "bina"])
        wb = self.book()  # round trip: nothing to change
        plan = self.read(wb)
        self.assertEqual([r.action for r in plan.rows["Insurance"]], ["same"])
        wb = self.book()
        ws = wb["Insurance"]
        heads = [c.value.rstrip(" *") for c in ws[1]]
        ws.cell(2, heads.index("Responsible (user names)") + 1).value = "anil"
        plan = self.read(wb)
        self.assertIn("responsible", plan.rows["Insurance"][0].changes)
        bulk_import.apply(plan)
        self.assertEqual([u.username for u in p.responsible.all()], ["anil"])
        bad = self.book()
        self.add(bad, self.row(**{"Policy number": "X", "Responsible (user names)": "anil, ghost"}))
        self.assertIn("ghost", " ".join(self.errors(self.read(bad))))

    def test_new_policies_are_added_with_their_centres_and_responsible_person(self):
        from . import bulk_import
        User.objects.create_user("accts", password="Demo@12345", role=User.AGENT)
        wb = self.book()
        self.add(wb, self.row(**{"Centres": "KKP, SEC", "Sum insured": "₹50,00,000", "Premium": 42000.0,
                                 "Responsible (user names)": "accts", "First reminder (days)": 45.0,
                                 "Broker or agent": "Ravi 9876543210"}))
        self.add(wb, self.row(**{"Cover type": "building", "Centres": "All", "Policy number": "B-1"}))
        self.add(wb, self.row(**{"Cover type": "Building", "Centres": "", "Policy number": "B-2"}))
        plan = self.read(wb)
        self.assertEqual(self.errors(plan), [])
        counts, _ = bulk_import.apply(plan)
        self.assertEqual(counts["Insurance"]["new"], 3)
        f = InsurancePolicy.objects.get(policy_number="F-100")
        self.assertEqual({c.code for c in f.centres.all()}, {"KKP", "SEC"})
        self.assertEqual((f.cover_type, f.insurer, f.start_date, f.expiry_date), (self.fire, "New India",
                                                                                    _ins_date(2026, 4, 1), _ins_date(2027, 3, 31)))
        self.assertEqual((int(f.sum_insured), int(f.premium), f.remind_days, ", ".join(u.username for u in f.responsible.all()), f.contact),
                         (5000000, 42000, 45, "accts", "Ravi 9876543210"))
        self.assertEqual(f.documents.count(), 0)  # documents are added on the policy's page
        for n in ("B-1", "B-2"):
            p = InsurancePolicy.objects.get(policy_number=n)
            self.assertEqual(p.centres.count(), 0)
            self.assertEqual(p.location, "All centres")

    def test_an_existing_policy_is_found_by_insurer_number_and_start_date_and_changed(self):
        from . import bulk_import
        p = InsurancePolicy.objects.create(cover_type=self.fire, insurer="New India", policy_number="F-100",
                                           start_date=_ins_date(2026, 4, 1), expiry_date=_ins_date(2027, 3, 31),
                                           premium=1000)
        p.centres.set([self.kkp])
        wb = self.book()
        ws = wb["Insurance"]
        heads = [c.value.rstrip(" *") for c in ws[1]]
        ws.cell(2, heads.index("Expiry date") + 1, "30-06-2027")
        ws.cell(2, heads.index("Centres") + 1, "KKP, SEC")
        ws.cell(2, heads.index("Cancelled") + 1, "Yes")
        plan = self.read(wb)
        self.assertEqual(self.errors(plan), [])
        r = plan.rows["Insurance"][0]
        self.assertEqual(r.action, "change")
        self.assertTrue({"expiry date", "centres", "cancelled"} <= set(r.changes))
        bulk_import.apply(plan)
        p.refresh_from_db()
        self.assertEqual(p.expiry_date, _ins_date(2027, 6, 30))
        self.assertTrue(p.cancelled)
        self.assertEqual({c.code for c in p.centres.all()}, {"KKP", "SEC"})
        self.assertEqual(InsurancePolicy.objects.count(), 1)

    def test_the_same_number_with_another_start_date_is_a_new_policy(self):
        from . import bulk_import
        InsurancePolicy.objects.create(cover_type=self.fire, insurer="New India", policy_number="F-100",
                                       start_date=_ins_date(2025, 4, 1), expiry_date=_ins_date(2026, 3, 31))
        wb = self.book()
        self.add(wb, self.row())
        plan = self.read(wb)
        self.assertEqual(plan.counts("Insurance")["new"], 1)
        bulk_import.apply(plan)
        self.assertEqual(InsurancePolicy.objects.filter(policy_number="F-100").count(), 2)

    def test_renewed_flag_stops_a_policy_counting(self):
        from . import bulk_import
        wb = self.book()
        self.add(wb, self.row(**{"Renewed": "Yes"}))
        bulk_import.apply(self.read(wb))
        self.assertTrue(InsurancePolicy.objects.get().renewed)

    def test_rows_may_refer_to_a_centre_and_person_added_in_the_same_file(self):
        from . import bulk_import
        wb = self.book()
        self.add(wb, {"Code": "MDP", "Name": "Madhapur"}, "Centres")
        self.add(wb, {"User name": "newbie", "Role": "Agent"}, "People")
        self.add(wb, self.row(**{"Centres": "MDP", "Responsible (user names)": "newbie"}))
        plan = self.read(wb)
        self.assertEqual(self.errors(plan), [])
        bulk_import.apply(plan)
        p = InsurancePolicy.objects.get()
        self.assertEqual([c.code for c in p.centres.all()], ["MDP"])
        self.assertEqual([u.username for u in p.responsible.all()], ["newbie"])

    def test_every_kind_of_mistake_is_reported_and_nothing_is_saved(self):
        from . import bulk_import
        wb = self.book()
        self.add(wb, self.row(**{"Cover type": "Marine"}))
        self.add(wb, self.row(**{"Policy number": "F-2", "Start date": "soon"}))
        self.add(wb, self.row(**{"Policy number": "F-3", "Expiry date": "01-01-2026"}))  # before the start
        self.add(wb, self.row(**{"Policy number": "F-4", "Centres": "ZZZ"}))
        self.add(wb, self.row(**{"Policy number": "F-5", "Responsible (user names)": "nobody"}))
        self.add(wb, self.row(**{"Policy number": "F-6", "Sum insured": "lots"}))
        self.add(wb, self.row(**{"Policy number": "F-7", "First reminder (days)": 0}))
        self.add(wb, self.row(**{"Policy number": "F-8", "Expiry date": ""}))
        self.add(wb, self.row(**{"Policy number": "", "Insurer": ""}))
        self.add(wb, self.row(**{"Policy number": "F-9", "Cancelled": "maybe"}))
        plan = self.read(wb)
        text = " ".join(self.errors(plan))
        for part in ("no type called 'Marine'", "Start date", "before the start date", "no centre with the code ZZZ",
                     "no user called 'nobody'", "is not an amount", "between 1 and 365", "Expiry date is empty",
                     "Insurer is empty", "Policy number is empty", "Use Yes or No"):
            self.assertIn(part, text)
        self.assertEqual(len(plan.errors), 10)
        with self.assertRaises(ValueError):
            bulk_import.apply(plan)
        self.assertEqual(InsurancePolicy.objects.count(), 0)

    def test_the_same_policy_twice_in_one_file_is_an_error(self):
        wb = self.book()
        self.add(wb, self.row())
        self.add(wb, self.row())
        self.assertIn("Same as row", " ".join(self.errors(self.read(wb))))

    def test_an_older_workbook_without_the_sheet_still_imports(self):
        wb = self.book()
        del wb["Insurance"]
        plan = self.read(wb)
        self.assertEqual(plan.errors, [])
        self.assertIn("Insurance", plan.missing_sheets)

    def test_the_import_page_accepts_the_file_and_shows_the_sheet(self):
        admin = User.objects.create_user("boss", password="Demo@12345", role=User.ADMIN, email="boss@example.com")
        self.client.force_login(admin)
        wb = self.book()
        self.add(wb, self.row())
        up = _InsFile("book.xlsx", self.save(wb), content_type="application/octet-stream")
        page = self.client.post(reverse("import_page"), {"step": "check", "file": up})
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, "Insurance")
        self.assertContains(page, "Fire F-100")
