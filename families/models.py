import uuid
from django.db import models
from django.utils import timezone
from dashboard.models import Family, Invoice, Lesson


from accounts.models import AccessLink


class Checkout(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    invoice = models.OneToOneField(Invoice, on_delete=models.PROTECT)
    lesson = models.ForeignKey(Lesson, null=True, blank=True, on_delete=models.PROTECT)
    provider = models.CharField(max_length=16)
    provider_id = models.CharField(max_length=100, null=True, blank=True, unique=True)
    confirmation_url = models.URLField(max_length=1000, blank=True)
    status = models.CharField(max_length=16, default="pending")
    documents_prepared = models.BooleanField(default=False)
    documents = models.ManyToManyField("PaymentDocument", blank=True)
    created_at = models.DateTimeField(auto_now_add=True)


def document_path(instance, filename):
    from pathlib import Path
    return f"documents/{uuid.uuid4().hex}{Path(filename).suffix.lower()}"


class PaymentDocument(models.Model):
    title = models.CharField("Название", max_length=200)
    file = models.FileField("Файл PDF или TXT", upload_to=document_path)
    sha256 = models.CharField(max_length=64, editable=False)
    active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]


class DocumentConsent(models.Model):
    checkout = models.ForeignKey(Checkout, on_delete=models.PROTECT, related_name="consents")
    document = models.ForeignKey(PaymentDocument, on_delete=models.PROTECT)
    accepted_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["checkout", "document"], name="one_consent_per_checkout_document")]
