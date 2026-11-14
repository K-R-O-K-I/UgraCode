import uuid
from django.db import models
from django.utils import timezone
from dashboard.models import Family, Invoice


class AccessLink(models.Model):
    family = models.ForeignKey(Family, on_delete=models.PROTECT, related_name="access_links")
    token_hash = models.CharField(max_length=64, unique=True)
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField()
    revoked_at = models.DateTimeField(null=True, blank=True)

    @property
    def valid(self):
        return self.revoked_at is None and self.expires_at > timezone.now()


class Checkout(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    invoice = models.OneToOneField(Invoice, on_delete=models.PROTECT)
    provider = models.CharField(max_length=16)
    provider_id = models.CharField(max_length=100, null=True, blank=True, unique=True)
    confirmation_url = models.URLField(max_length=1000, blank=True)
    status = models.CharField(max_length=16, default="pending")
    created_at = models.DateTimeField(auto_now_add=True)
