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
    created_at = models.DateTimeField(auto_now_add=True)
