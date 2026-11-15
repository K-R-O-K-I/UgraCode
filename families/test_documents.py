import tempfile
import uuid
from datetime import timedelta
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.exceptions import ValidationError
from django.test import TestCase, override_settings
from django.utils import timezone
from accounts.models import Family, Child
from dashboard.models import PassPlan, Invoice, Payment, Receipt, Lesson, LessonPolicy
from .models import Checkout, PaymentDocument, DocumentConsent
from .services import create_checkout, complete, issue_link
from .documents import prepare


@override_settings(DEBUG=True, PAYMENT_MODE="demo")
class DocumentTests(TestCase):
    def setUp(self):
        self.media = tempfile.TemporaryDirectory()
        self.addCleanup(self.media.cleanup)
        self.override = override_settings(MEDIA_ROOT=self.media.name)
        self.override.enable()
        self.addCleanup(self.override.disable)
        self.family = Family.objects.create(contact_name="Тестовая семья")
        self.child = Child.objects.create(family=self.family, name="Ребёнок")
        self.plan = PassPlan.objects.create(name="Тариф", price=100, lessons=1, validity_days=30, public=True)
        self.document = PaymentDocument.objects.create(title="Согласие v1", file=SimpleUploadedFile("consent.txt", b"example"), sha256="x"*64)
        self.checkout = create_checkout(self.family, self.child, self.plan, uuid.uuid4(), "demo")
        self.url = f"/family/checkout/{self.checkout.pk}/"
        self.client.get(f"/family/enter/{issue_link(self.family)}/")

    def test_checkbox_is_required_on_server_and_direct_completion(self):
        response = self.client.post(self.url, {"action":"confirm"})
        self.assertContains(response, "отметьте согласие")
        self.assertFalse(Payment.objects.exists())
        with self.assertRaises(ValidationError):
            complete(self.checkout.pk)
        self.assertEqual(self.client.post(self.url, {"action":"confirm", "documents":[self.document.pk]}).status_code,302)
        self.client.post(self.url, {"action":"confirm", "documents":[self.document.pk]})
        self.assertEqual(Payment.objects.count(),1)
        self.assertEqual(DocumentConsent.objects.count(),1)

    def test_snapshot_remains_after_new_document_and_archive(self):
        self.client.get(self.url)
        self.document.active = False
        self.document.save()
        PaymentDocument.objects.create(title="Новая версия", file="new.txt",sha256="y"*64)
        response = self.client.get(self.url)
        self.assertContains(response,"Согласие v1")
        self.assertNotContains(response,"Новая версия")
        download = self.client.get(f"/document/{self.document.pk}/")
        self.assertEqual(download.status_code,200)
        self.assertEqual(b"".join(download.streaming_content), b"example")
        response = self.client.post(self.url,{"action":"confirm","documents":[self.document.pk]})
        self.assertEqual(response.status_code,302)

    def test_other_family_cannot_access_receipt_checkout_or_archived_document(self):
        other = Family.objects.create(contact_name="Другая семья")
        child = Child.objects.create(family=other,name="Чужой ребёнок")
        invoice = Invoice.objects.create(child=child,plan=self.plan,amount=100,lessons=1,validity_days=30)
        payment = Payment.objects.create(invoice=invoice,amount=100,method="cash")
        receipt = Receipt.objects.create(payment=payment,external_url="https://example.com/receipt")
        other_document = PaymentDocument.objects.create(title="Чужое согласие",file="private.txt",sha256="z"*64,active=False)
        self.assertEqual(self.client.get(f"/family/receipt/{receipt.pk}/").status_code,404)
        self.assertEqual(self.client.get(f"/document/{other_document.pk}/").status_code,404)
        self.assertNotContains(self.client.get("/family/?page=receipts"),"Чужой ребёнок")
        own_invoice = Invoice.objects.create(child=self.child,plan=self.plan,amount=100,lessons=1,validity_days=30)
        own_payment = Payment.objects.create(invoice=own_invoice,amount=100,method="cash")
        own_receipt = Receipt.objects.create(payment=own_payment,external_url="https://example.com/own")
        self.assertContains(self.client.get("/family/?page=receipts"),f"/family/receipt/{own_receipt.pk}/")

    def test_staff_document_upload_and_archive(self):
        self.assertEqual(self.client.get("/documents/").status_code,302)
        user=get_user_model().objects.create_user("staff",is_staff=True)
        self.client.force_login(user)
        self.assertEqual(self.client.get("/documents/").status_code,200)
        response=self.client.post("/documents/",{"title":"Новый файл","file":SimpleUploadedFile("terms.txt",b"Terms")})
        self.assertEqual(response.status_code,302)
        item=PaymentDocument.objects.get(title="Новый файл")
        self.assertEqual(len(item.sha256),64)
        self.client.post("/documents/",{"action":"archive","document":item.pk})
        item.refresh_from_db()
        self.assertFalse(item.active)
        response=self.client.post("/documents/",{"title":"Bad","file":SimpleUploadedFile("terms.html",b"<script>")})
        self.assertContains(response,"Загрузите PDF")

    def test_calendar_ranges_and_boundaries(self):
        self.client.force_login(get_user_model().objects.create_user("staff",is_staff=True))
        policy=LessonPolicy.objects.create()
        day=timezone.localdate().replace(day=15)
        for offset in (-16,-3,0,3,18):
            start=timezone.localtime().replace(year=day.year,month=day.month,day=day.day)+timedelta(days=offset)
            Lesson.objects.create(title=f"Lesson {offset}",starts_at=start,ends_at=start+timedelta(hours=1),capacity=4,policy=policy)
        for period in ("day","week","month"):
            response=self.client.get("/",{"page":"journal","date":day.isoformat(),"period":period})
            self.assertEqual(response.status_code,200)
            for lesson in response.context["lessons"]:
                self.assertTrue(response.context["range_start"] <= timezone.localdate(lesson.starts_at) <= response.context["range_end"])
        response=self.client.get("/",{"page":"journal","date":"2026-12-31","period":"month"})
        self.assertEqual(response.context["following"].isoformat(),"2027-01-01")
        self.assertContains(response,"Месяц")
