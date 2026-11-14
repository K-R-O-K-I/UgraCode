import uuid
from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch
from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.core.management import call_command
from django.test import Client, TestCase, override_settings
from django.utils import timezone
from dashboard import services as master
from dashboard.models import Booking, Child, Family, Invoice, Lesson, Notification, PassPlan, Payment, Receipt
from . import services
from .models import AccessLink, Checkout


@override_settings(DEBUG=True, PAYMENT_MODE="demo")
class FamilyTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.master = get_user_model().objects.create_user("master", password="test-only", is_staff=True)
        call_command("demo_data", verbosity=0)
        call_command("family_demo", verbosity=0)

    def setUp(self):
        cache.clear()
        self.family = Family.objects.first()
        self.other = Family.objects.exclude(pk=self.family.pk).first()
        self.child = self.family.children.first()
        self.other_child = self.other.children.first()
        self.token = services.issue_link(self.family)
        self.client.get(f"/family/enter/{self.token}/")
        self.plan = PassPlan.objects.filter(public=True, lessons=4).get()
        self.lesson = Lesson.objects.create(
            title="Будущее занятие", starts_at=timezone.now()+timedelta(days=2),
            ends_at=timezone.now()+timedelta(days=2,hours=1), capacity=2,
            policy=Lesson.objects.first().policy,
        )

    def purchase(self, key=None):
        return services.create_checkout(self.family, self.child, self.plan, key or uuid.uuid4(), "demo")

    def test_family_pages_have_navigation_and_no_other_families(self):
        for page in ("calendar", "upcoming", "children", "visits", "payments", "notifications"):
            response = self.client.get("/family/", {"page": page})
            self.assertEqual(response.status_code, 200, page)
            self.assertContains(response, 'src="/static/app.js"')
            self.assertNotContains(response, self.other.contact_name)
            self.assertNotContains(response, self.other_child.name)
        self.assertEqual(self.client.get("/").status_code, 302)

    def test_only_token_hash_is_saved(self):
        link = AccessLink.objects.get(family=self.family, revoked_at=None)
        self.assertNotEqual(link.token_hash, self.token)
        self.assertEqual(len(link.token_hash), 64)
        self.assertEqual(self.client.get("/family/enter/1/").status_code, 403)

    def test_revocation_closes_existing_session(self):
        services.revoke_links(self.family)
        self.assertEqual(self.client.get("/family/").status_code, 403)
        self.assertEqual(self.client.get(f"/family/enter/{self.token}/").status_code, 403)

    def test_expiry_and_rotation(self):
        AccessLink.objects.filter(family=self.family).update(expires_at=timezone.now()-timedelta(seconds=1))
        self.assertEqual(self.client.get("/family/").status_code, 403)
        fresh = services.issue_link(self.family)
        self.assertEqual(self.client.get(f"/family/enter/{fresh}/").status_code, 302)
        services.issue_link(self.family)
        self.assertEqual(self.client.get("/family/").status_code, 403)

    def test_child_added_to_current_family_ignores_posted_family(self):
        response = self.client.post("/family/form/child/", {"name": "Новый ребёнок", "family": self.other.pk})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(Child.objects.get(name="Новый ребёнок").family_id, self.family.pk)

    def test_cannot_book_or_pay_for_other_child(self):
        response = self.client.post("/family/form/book/", {"child": self.other_child.pk, "lesson": self.lesson.pk})
        self.assertEqual(response.status_code, 200)
        self.assertFalse(Booking.objects.filter(lesson=self.lesson).exists())
        response = self.client.post("/family/form/buy/", {"child": self.other_child.pk, "plan": self.plan.pk, "key": uuid.uuid4()})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(Checkout.objects.count(), 0)

    def test_booking_cancel_reason_visible_to_master(self):
        response = self.client.post("/family/form/book/", {"child": self.child.pk, "lesson": self.lesson.pk})
        self.assertEqual(response.status_code, 302)
        self.client.post(response.url, {"action": "confirm"})
        booking = Booking.objects.get(lesson=self.lesson, child=self.child)
        self.client.post(f"/family/form/cancel/?booking={booking.pk}", {"reason": "Заболел"})
        booking.refresh_from_db()
        self.assertEqual(booking.status, "cancelled")
        self.assertEqual(booking.parent_reason, "Заболел")
        staff = Client()
        staff.force_login(self.master)
        response = staff.get("/", {"date": timezone.localdate(self.lesson.starts_at), "lesson": self.lesson.pk})
        self.assertContains(response, "Заболел")

    def test_other_bookings_are_inaccessible(self):
        booking = Booking.objects.filter(child=self.other_child).first()
        for action in ("cancel", "reason"):
            self.assertEqual(self.client.get(f"/family/form/{action}/?booking={booking.pk}").status_code, 404)
            self.assertEqual(self.client.post(f"/family/form/{action}/?booking={booking.pk}", {"reason": "x"}).status_code, 404)

    def test_payment_is_idempotent_and_amount_is_server_owned(self):
        key = uuid.uuid4()
        balance = self.child.balance
        data = {"child": self.child.pk, "plan": self.plan.pk, "key": key, "amount": "1"}
        self.client.post("/family/form/buy/", data)
        self.client.post("/family/form/buy/", data)
        self.assertEqual(Checkout.objects.count(), 1)
        checkout = Checkout.objects.get(pk=key)
        self.assertEqual(checkout.invoice.amount, self.plan.price)
        self.assertEqual(self.child.balance, balance)
        self.client.get(f"/family/checkout/{key}/")
        self.assertEqual(self.child.balance, balance)
        self.client.post(f"/family/checkout/{key}/", {"action": "confirm"})
        self.client.post(f"/family/checkout/{key}/", {"action": "confirm"})
        self.assertEqual(self.child.balance, balance+4)
        self.assertEqual(Payment.objects.filter(operation_key=key).count(), 1)

    def test_payment_cancel_and_foreign_checkout(self):
        checkout = self.purchase()
        outsider = Client()
        outsider.get(f"/family/enter/{services.issue_link(self.other)}/")
        self.assertEqual(outsider.get(f"/family/checkout/{checkout.pk}/").status_code, 404)
        self.client.post(f"/family/checkout/{checkout.pk}/", {"action": "cancel"})
        checkout.refresh_from_db()
        self.assertEqual(checkout.status, "cancelled")
        response = self.client.post(f"/family/checkout/{checkout.pk}/", {"action": "confirm"})
        self.assertContains(response, "уже отменён")
        self.assertFalse(Payment.objects.filter(operation_key=checkout.pk).exists())

    def test_receipt_ownership_and_revoked_session(self):
        payment = master.pay(self.master, {"child": self.child, "amount": Decimal("4800"), "lessons": 4,
            "validity_days": 30, "method": "cash", "operation_key": uuid.uuid4()})
        receipt = Receipt.objects.create(payment=payment, external_url="https://example.com/receipt")
        self.assertEqual(self.client.get(f"/family/receipt/{receipt.pk}/").status_code, 302)
        outsider = Client()
        outsider.get(f"/family/enter/{services.issue_link(self.other)}/")
        self.assertEqual(outsider.get(f"/family/receipt/{receipt.pk}/").status_code, 404)
        services.revoke_links(self.family)
        self.assertEqual(self.client.get(f"/family/receipt/{receipt.pk}/").status_code, 403)

    def test_notifications_are_private_and_parent_cannot_read_master_messages(self):
        Notification.objects.create(text="STAFF-PRIVATE")
        Notification.objects.create(family=self.other, text="OTHER-PRIVATE")
        Notification.objects.create(family=self.family, text="OWN-PRIVATE")
        response = self.client.get("/family/?page=notifications")
        self.assertContains(response, "OWN-PRIVATE")
        self.assertNotContains(response, "OTHER-PRIVATE")
        self.assertNotContains(response, "STAFF-PRIVATE")

    def test_csrf_and_staff_only_management(self):
        strict = Client(enforce_csrf_checks=True)
        strict.get(f"/family/enter/{self.token}/")
        self.assertEqual(strict.post("/family/form/child/", {"name": "Test"}).status_code, 403)
        self.assertEqual(self.client.get("/families/").status_code, 302)

    @override_settings(DEBUG=False)
    def test_demo_confirmation_disabled_outside_debug(self):
        checkout = self.purchase()
        self.assertEqual(self.client.post(f"/family/checkout/{checkout.pk}/", {"action": "confirm"}).status_code, 403)

    @override_settings(PAYMENT_MODE="yookassa")
    def test_provider_webhook_checks_server_data_and_retries(self):
        from .payments import verify
        checkout = self.purchase()
        checkout.provider = "yookassa"
        checkout.provider_id = "provider-example"
        checkout.save()
        payload = {"id": "provider-example", "status": "succeeded", "paid": True,
                   "amount": {"value": "4800.00", "currency": "RUB"},
                   "metadata": {"invoice_id": str(checkout.invoice_id), "checkout_id": str(checkout.pk)}}
        with patch("families.payments.api", return_value=payload):
            verify(checkout)
            verify(checkout)
        self.assertEqual(Payment.objects.filter(operation_key=checkout.pk).count(), 1)

    @override_settings(PAYMENT_MODE="yookassa")
    def test_wrong_provider_amount_never_grants(self):
        checkout = self.purchase()
        checkout.provider = "yookassa"
        checkout.provider_id = "provider-example"
        checkout.save()
        payload = {"id": "provider-example", "status": "succeeded", "paid": True,
                   "amount": {"value": "1.00", "currency": "RUB"},
                   "metadata": {"invoice_id": str(checkout.invoice_id), "checkout_id": str(checkout.pk)}}
        with patch("families.payments.api", return_value=payload):
            response = self.client.post("/family/webhook/", {"object": {"id": checkout.provider_id}}, content_type="application/json")
        self.assertEqual(response.status_code, 503)
        self.assertFalse(Payment.objects.filter(operation_key=checkout.pk).exists())

    def test_link_entry_and_forms_preserve_csrf_protection(self):
        browser = Client(enforce_csrf_checks=True)
        for route in ("access", "enter"):
            response = browser.get(f"/family/{route}/{self.token}/")
            self.assertEqual(response.status_code, 302)
            self.assertEqual(response["Referrer-Policy"], "no-referrer")
        response = browser.get("/family/form/child/")
        self.assertEqual(response["Referrer-Policy"], "same-origin")
        token = browser.cookies["csrftoken"].value
        endpoint = "/family/form/child/"
        data = {"name": "CSRF test child", "csrfmiddlewaretoken": token}
        self.assertEqual(browser.post(endpoint, data, HTTP_ORIGIN="null").status_code, 403)
        self.assertEqual(browser.post(endpoint, data, HTTP_ORIGIN="https://other.example").status_code, 403)
        self.assertEqual(browser.post(endpoint, {"name": "Missing token"}, HTTP_ORIGIN="http://testserver").status_code, 403)
        self.assertEqual(browser.post(endpoint, data, HTTP_ORIGIN="http://testserver").status_code, 302)
        self.assertTrue(self.family.children.filter(name="CSRF test child").exists())
        staff = Client(enforce_csrf_checks=True)
        staff.force_login(self.master)
        response = staff.get("/families/")
        self.assertEqual(response["Referrer-Policy"], "same-origin")
        response = staff.post(f"/families/{self.family.pk}/issue/", {
            "csrfmiddlewaretoken": staff.cookies["csrftoken"].value,
        }, HTTP_ORIGIN="http://testserver")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Referrer-Policy"], "same-origin")

    def test_parent_updates_child_and_avatar_access_is_private(self):
        import io
        import tempfile
        from PIL import Image
        from django.core.files.uploadedfile import SimpleUploadedFile
        with tempfile.TemporaryDirectory() as media, override_settings(MEDIA_ROOT=media):
            picture = io.BytesIO()
            Image.new("RGB", (20, 20)).save(picture, format="PNG")
            response = self.client.post(f"/family/form/child/?child={self.child.pk}", {
                "name": "Новое имя", "age": 9, "description": "Любит мастерить",
                "avatar": SimpleUploadedFile("avatar.png", picture.getvalue(), content_type="image/png"),
            })
            self.assertEqual(response.status_code, 302)
            self.child.refresh_from_db()
            self.assertEqual(self.child.age, 9)
            self.assertEqual(self.child.description, "Любит мастерить")
            response = self.client.get(f"/children/{self.child.pk}/avatar/")
            self.assertEqual(response.status_code, 200)
            self.assertEqual(Client().get(f"/children/{self.child.pk}/avatar/").status_code, 404)
            outsider = Client()
            outsider.get(f"/family/enter/{services.issue_link(self.other)}/")
            self.assertEqual(outsider.get(f"/children/{self.child.pk}/avatar/").status_code, 404)
            self.assertEqual(outsider.post(f"/family/form/child/?child={self.child.pk}", {"name": "Bad"}).status_code, 404)
            self.assertContains(self.client.get("/family/?page=children"), "Возраст: 9")
            self.assertTrue(b"".join(response.streaming_content))

    def test_child_rejects_invalid_age_and_non_image(self):
        from django.core.files.uploadedfile import SimpleUploadedFile
        from .forms import ChildForm
        for age in (-1, 101):
            self.assertFalse(ChildForm({"name": "Ребёнок", "age": age}).is_valid())
        form = ChildForm({"name": "Ребёнок"}, {"avatar": SimpleUploadedFile("bad.png", b"not an image")})
        self.assertFalse(form.is_valid())

    def test_booking_payment_is_idempotent(self):
        data = {"child": self.child.pk, "lesson": self.lesson.pk}
        first = self.client.post("/family/form/book/", data)
        second = self.client.post("/family/form/book/", data)
        self.assertEqual(first.url, second.url)
        self.assertFalse(Booking.objects.filter(lesson=self.lesson, child=self.child).exists())
        for _ in range(2):
            self.assertEqual(self.client.post(first.url, {"action": "confirm"}).status_code, 302)
        checkout = Checkout.objects.get(lesson=self.lesson, invoice__child=self.child)
        self.assertEqual(Payment.objects.filter(invoice=checkout.invoice).count(), 1)
        self.assertEqual(Booking.objects.filter(lesson=self.lesson, child=self.child).count(), 1)

    def test_filled_lesson_leaves_booking_checkout_unpaid(self):
        self.lesson.capacity = 1
        self.lesson.save(update_fields=["capacity"])
        response = self.client.post("/family/form/book/", {"child": self.child.pk, "lesson": self.lesson.pk})
        checkout = Checkout.objects.get(lesson=self.lesson, invoice__child=self.child)
        master.book(self.master, {"lesson": self.lesson, "child": self.other_child})
        self.assertContains(self.client.post(response.url, {"action": "confirm"}), "Свободных мест больше нет")
        self.assertFalse(Payment.objects.filter(invoice=checkout.invoice).exists())
        self.assertFalse(Booking.objects.filter(lesson=self.lesson, child=self.child).exists())

    @override_settings(PAYMENT_MODE="disabled")
    def test_booking_payment_disabled(self):
        response = self.client.post("/family/form/book/", {"child": self.child.pk, "lesson": self.lesson.pk})
        self.assertEqual(response.status_code, 200)
        self.assertFalse(Checkout.objects.filter(lesson=self.lesson).exists())
