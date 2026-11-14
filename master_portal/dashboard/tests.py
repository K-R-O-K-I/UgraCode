import uuid
from datetime import timedelta
from decimal import Decimal
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.test import Client, TestCase
from django.utils import timezone
from . import services
from .models import Booking, Child, Lesson, LessonEntry, LessonPolicy, Payment, Subscription


class MasterDashboardTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.master = get_user_model().objects.create_user("master", password="test-only-password", is_staff=True)
        call_command("demo_data", verbosity=0)
        cls.lesson = Lesson.objects.first()
        cls.child = Child.objects.first()

    def setUp(self):
        self.client.force_login(self.master)

    def test_staff_pages_render_without_scripts(self):
        for page in ("schedule", "journal", "children", "payments", "history", "settings", "notifications"):
            response = self.client.get("/", {"page": page})
            self.assertEqual(response.status_code, 200, page)
            self.assertNotContains(response, "<script")
        for action in ("lesson", "booking", "child", "payment", "policy"):
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

    def test_get_does_not_modify_and_post_creates_lesson(self):
        count = Lesson.objects.count()
        self.client.get("/form/lesson/")
        self.assertEqual(Lesson.objects.count(), count)
        start = timezone.localtime()+timedelta(days=2)
        response = self.client.post("/form/lesson/", {
            "title": "Кормушка", "starts_at": start.strftime("%Y-%m-%dT%H:%M"),
            "ends_at": (start+timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M"),
            "kind": "group", "capacity": 2, "description": "Дерево и клей",
        })
        self.assertEqual(response.status_code, 302)
        self.assertEqual(Lesson.objects.count(), count+1)
