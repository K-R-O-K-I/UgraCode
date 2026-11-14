import uuid
from datetime import timedelta
from decimal import Decimal
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.test import Client, TestCase
from django.utils import timezone
from . import services
from .models import Booking, Child, Lesson, LessonEntry, LessonPolicy, Notification, Payment, Subscription


class MasterDashboardTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.master = get_user_model().objects.create_user("master", password="test-only-password", is_staff=True)
        call_command("demo_data", verbosity=0)
        cls.lesson = Lesson.objects.first()
        cls.child = Child.objects.first()

    def setUp(self):
        self.client.force_login(self.master)

    def test_staff_pages_render_with_progressive_navigation(self):
        for page in ("schedule", "journal", "children", "payments", "history", "settings", "notifications"):
            response = self.client.get("/", {"page": page})
            self.assertEqual(response.status_code, 200, page)
            self.assertContains(response, 'src="/static/app.js?')
        for action in ("lesson", "payment"):
            self.assertEqual(self.client.get(f"/form/{action}/").status_code, 200)

    def test_authentication_and_csrf(self):
        guest = Client()
        self.assertEqual(guest.get("/").status_code, 302)
        parent = get_user_model().objects.create_user("parent")
        guest.force_login(parent)
        self.assertEqual(guest.get("/").status_code, 403)
        strict = Client(enforce_csrf_checks=True)
        strict.force_login(self.master)
        self.assertEqual(strict.post("/form/child/", {"name": "Test", "parent": "Test"}).status_code, 403)

    def test_attendance_is_idempotent_and_correction_restores_once(self):
        booking = Booking.objects.filter(lesson=self.lesson, child=self.child).get()
        payload = {"booking": booking, "attendance": "present", "reason": ""}
        services.mark(self.master, payload)
        services.mark(self.master, payload)
        self.assertEqual(self.child.balance, 3)
        services.mark(self.master, {**payload, "attendance": "pending", "reason": "Исправление"})
        services.mark(self.master, {**payload, "attendance": "pending", "reason": "Исправление"})
        self.assertEqual(self.child.balance, 4)
        self.assertEqual(LessonEntry.objects.filter(booking=booking).count(), 2)

    def test_one_lesson_notice_is_family_scoped_and_repeats_after_refill(self):
        notes = Notification.objects.filter(
            family=self.child.family, text__contains="осталось одно занятие",
        )
        subscription = Subscription.objects.get(invoice__child=self.child)
        LessonEntry.objects.filter(subscription=subscription).update(delta=2)
        booking = Booking.objects.get(lesson=self.lesson, child=self.child)
        payload = {"booking": booking, "attendance": "present"}
        services.mark(self.master, payload)
        services.mark(self.master, payload)
        services.sync_low_balance_notice(self.child.pk)
        self.assertEqual(notes.count(), 1)
        self.assertIn(self.child.name, notes.get().text)
        self.assertIsNone(notes.get().read_at)
        self.assertFalse(Notification.objects.filter(
            text__contains=f"{self.child.name} · осталось одно занятие",
        ).exclude(family=self.child.family).exists())

        services.mark(self.master, {**payload, "attendance": "pending", "reason": "Исправление"})
        services.mark(self.master, payload)
        self.assertEqual(notes.count(), 2)

    def test_failed_attendance_rolls_back(self):
        booking = Booking.objects.filter(lesson=self.lesson, child=self.child).get()
        Subscription.objects.filter(invoice__child=self.child).update(expires_on=timezone.localdate()-timedelta(days=1))
        count = LessonEntry.objects.count()
        with self.assertRaises(ValidationError):
            services.mark(self.master, {"booking": booking, "attendance": "present", "reason": ""})
        self.assertEqual(LessonEntry.objects.count(), count)
        booking.refresh_from_db()
        self.assertEqual(booking.attendance, "pending")

    def future_lesson(self, capacity=1):
        start = timezone.now()+timedelta(days=1)
        return Lesson.objects.create(title="Test", starts_at=start, ends_at=start+timedelta(hours=1), capacity=capacity, policy=self.lesson.policy)

    def test_duplicate_and_overcapacity_booking(self):
        target = self.future_lesson()
        payload = {"lesson": target, "child": self.child}
        services.book(self.master, payload)
        with self.assertRaises(ValidationError):
            services.book(self.master, payload)
        with self.assertRaises(ValidationError):
            services.book(self.master, {"lesson": target, "child": Child.objects.exclude(pk=self.child.pk).first()})
        self.assertEqual(target.active_bookings.count(), 1)

    def test_full_group_transfer_preserves_original(self):
        target = self.future_lesson()
        Booking.objects.create(lesson=target, child=Child.objects.exclude(pk=self.child.pk).first())
        booking = Booking.objects.get(lesson=self.lesson, child=self.child)
        with self.assertRaises(ValidationError):
            services.move(self.master, {"booking": booking, "lesson": target, "reason": "Test"})
        booking.refresh_from_db()
        self.assertEqual(booking.status, "booked")

    def test_payment_retry_only_grants_once(self):
        payload = {"child": self.child, "amount": Decimal("4800"), "lessons": 4, "validity_days": 30, "method": "cash", "operation_key": uuid.uuid4()}
        services.pay(self.master, payload)
        services.pay(self.master, payload)
        self.assertEqual(Payment.objects.count(), 1)
        self.assertEqual(self.child.balance, 8)

    def test_cancellation_restores_charge_and_preserves_policy(self):
        booking = Booking.objects.get(lesson=self.lesson, child=self.child)
        services.mark(self.master, {"booking": booking, "attendance": "present", "reason": ""})
        services.policy(self.master, {"free_cancel_hours": 48, "charge_absence": False, "charge_late_cancel": False})
        self.lesson.refresh_from_db()
        self.assertEqual(self.lesson.policy.free_cancel_hours, 12)
        services.cancel(self.master, {"lesson": self.lesson, "reason": "Test"})
        services.cancel(self.master, {"lesson": self.lesson, "reason": "Test"})
        self.assertEqual(self.child.balance, 4)

    def test_schedule_journal_and_individual_capacity(self):
        from .forms import LessonForm
        start = timezone.localtime() + timedelta(days=2)
        form = LessonForm({
            "title": "Индивидуальное", "kind": "individual",
            "starts_at": start, "ends_at": start + timedelta(hours=1),
            "free_cancel_hours": 12,
        })
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data["capacity"], 1)
        response = self.client.get("/", {"date": "2000-01-01"})
        self.assertEqual(response.context["day"], timezone.localdate())
        self.assertNotContains(response, "Создать занятие")
        response = self.client.get("/?page=journal")
        self.assertContains(response, "Создать занятие")
        self.assertContains(response, 'class="inline-attendance"')
        self.assertEqual(self.client.post(f"/form/lesson/?id={self.lesson.pk}", {}).status_code, 404)
        response = self.client.get("/families/", {"q": self.child.name})
        self.assertContains(response, self.child.name)
        self.assertEqual(self.client.get(f"/children/{self.child.pk}/").status_code, 200)

    def test_get_does_not_modify_and_post_creates_lesson(self):
        count = Lesson.objects.count()
        self.client.get("/form/lesson/")
        self.assertEqual(Lesson.objects.count(), count)
        start = timezone.localtime()+timedelta(days=2)
        response = self.client.post("/form/lesson/", {
            "title": "Кормушка", "starts_at": start.strftime("%Y-%m-%dT%H:%M"),
            "ends_at": (start+timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M"),
            "kind": "group", "capacity": 2, "description": "Дерево и клей",
            "free_cancel_hours": 24, "charge_absence": True,
        })
        self.assertEqual(response.status_code, 302)
        self.assertEqual(Lesson.objects.count(), count+1)
        self.assertEqual(Lesson.objects.get(title="Кормушка").policy.free_cancel_hours, 24)
        self.assertEqual(self.client.get("/form/booking/").status_code, 404)
        self.assertEqual(self.client.post("/form/booking/", {}).status_code, 404)
        self.assertEqual(self.client.get("/form/policy/").status_code, 404)

class MasterFamilyIntegrationTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.master = get_user_model().objects.create_user("owner", password="test-password", is_staff=True)
        call_command("demo_data", verbosity=0)

    def setUp(self):
        self.client.force_login(self.master)

    def test_family_link_and_delete_without_history(self):
        from accounts.models import Family
        from accounts.services import update_family_link
        family = Family.objects.create(contact_name="Test", phone="123")
        Child.objects.create(family=family, name="Test child")
        self.assertEqual(self.client.get("/families/").status_code, 200)
        response = self.client.post(f"/families/{family.pk}/issue/")
        self.assertEqual(response.status_code, 200)
        parent = Client()
        self.assertEqual(parent.get(response.context["link"], follow=True).status_code, 200)
        self.client.post(f"/families/{family.pk}/delete/")
        self.assertFalse(Family.objects.filter(pk=family.pk).exists())
        self.assertEqual(parent.get("/family/").status_code, 403)

    def test_archive_preserves_history_and_revokes_session(self):
        from accounts.services import update_family_link
        family = Child.objects.first().family
        token = update_family_link(family.pk, issue=True)
        parent = Client()
        parent.get(f"/family/access/{token}/")
        count = Booking.objects.count()
        self.client.post(f"/families/{family.pk}/delete/")
        family.refresh_from_db()
        self.assertFalse(family.active)
        self.assertEqual(Booking.objects.count(), count)
        self.assertEqual(parent.get("/family/").status_code, 403)

    def test_quick_attendance_and_correction(self):
        booking = Booking.objects.filter(child=Child.objects.first()).first()
        url = f"/attendance/{booking.pk}/"
        self.assertEqual(self.client.get(url).status_code, 405)
        self.assertEqual(self.client.post(url, {"attendance": "present"}).status_code, 302)
        self.client.post(url, {"attendance": "present"})
        self.assertEqual(booking.child.balance, 3)
        self.client.post(url, {"attendance": "pending"})
        self.assertEqual(booking.child.balance, 4)
        self.assertEqual(self.client.post(url, {"attendance": "bad"}).status_code, 302)

    def test_parent_cannot_manage_families(self):
        guest = Client()
        self.assertEqual(guest.post("/families/1/issue/").status_code, 302)
        strict = Client(enforce_csrf_checks=True)
        strict.force_login(self.master)
        self.assertEqual(strict.post("/families/1/delete/").status_code, 403)

    def test_master_creates_family_but_cannot_change_children(self):
        response = self.client.post("/families/new/", {
            "contact_name": "Семья Ивановых", "phone": "89221241235",
            "children-0-name": "Not allowed",
        })
        self.assertEqual(response.status_code, 302)
        from accounts.models import Family
        family = Family.objects.get(contact_name="Семья Ивановых")
        self.assertEqual(family.phone, "8 (922) 124 1235")
        self.assertFalse(family.children.exists())
        self.assertEqual(self.client.post(f"/families/{family.pk}/edit/", {}).status_code, 404)
        self.assertEqual(self.client.post("/form/child/", {}).status_code, 404)

    def test_phone_validation(self):
        from accounts.forms import FamilyForm
        for phone in ("123", "892212412350", "abc89221241235"):
            self.assertFalse(FamilyForm({"contact_name": "Семья", "phone": phone}).is_valid())
        self.assertTrue(FamilyForm({"contact_name": "Семья", "phone": "+7 (922) 124 1235"}).is_valid())
