import hashlib
import secrets
from datetime import timedelta

from django.db import transaction
from django.utils import timezone

from .models import AccessLink, Family


def hash_token(token):
    return hashlib.sha256(token.encode("ascii")).hexdigest()


@transaction.atomic
def update_family_link(family_id, *, issue):
    family = Family.objects.select_for_update().get(pk=family_id, active=True)
    now = timezone.now()
    family.access_links.filter(revoked_at__isnull=True).update(revoked_at=now)
    if issue:
        token = secrets.token_urlsafe(32)
        AccessLink.objects.create(
            family=family, token_hash=hash_token(token), expires_at=now + timedelta(days=90)
        )
        return token
