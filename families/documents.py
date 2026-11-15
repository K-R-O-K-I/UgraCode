from django.db import transaction
from django.core.exceptions import ValidationError
from .models import Checkout, PaymentDocument, DocumentConsent


@transaction.atomic
def prepare(checkout):
    locked = Checkout.objects.select_for_update().get(pk=checkout.pk)
    if not locked.documents_prepared:
        locked.documents.set(PaymentDocument.objects.filter(active=True))
        locked.documents_prepared = True
        locked.save(update_fields=["documents_prepared"])
    return locked


@transaction.atomic
def accept(checkout, selected):
    checkout = prepare(checkout)
    required = set(checkout.documents.values_list("pk", flat=True))
    if not {str(pk) for pk in required}.issubset(set(selected)):
        raise ValidationError("Ознакомьтесь с каждым документом и отметьте согласие перед оплатой.")
    for pk in required:
        DocumentConsent.objects.get_or_create(checkout=checkout, document_id=pk)


def ensure_accepted(checkout):
    checkout = prepare(checkout)
    if checkout.documents.exclude(pk__in=checkout.consents.values("document_id")).exists():
        raise ValidationError("Перед оплатой необходимо согласиться с документами.")
