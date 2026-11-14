from datetime import timedelta
from django.core.management.base import BaseCommand
from django.utils import timezone
from dashboard.models import Child, Invoice, Notification, Subscription
from dashboard.services import sync_low_balance_notice


class Command(BaseCommand):
    help = "Подготовить семейные уведомления об остатках, сроках и открытых счетах."

    def handle(self, *args, **options):
        today = timezone.localdate()
        def notify(family_id, text):
            Notification.objects.get_or_create(family_id=family_id, text=f"{today:%d.%m.%Y} · {text}")
        for child in Child.objects.filter(family__active=True):
            subscriptions = list(Subscription.objects.filter(
                invoice__child=child, starts_on__lte=today, expires_on__gte=today,
            ))
            sync_low_balance_notice(child.pk)
            for subscription in subscriptions:
                if subscription.balance > 0 and subscription.expires_on <= today+timedelta(days=7):
                    notify(child.family_id, f"{child.name} · абонемент действует до {subscription.expires_on:%d.%m}.")
        for invoice in Invoice.objects.filter(status="open").select_related("child"):
            notify(invoice.child.family_id, f"{invoice.child.name} · ожидает оплаты счёт на {invoice.amount} ₽.")
        self.stdout.write("Семейные уведомления подготовлены.")
