from django.core.management.base import BaseCommand
from django.db import transaction
from dashboard.models import PassPlan


class Command(BaseCommand):
    help = "Добавить публичные демонстрационные тарифы."

    @transaction.atomic
    def handle(self, *args, **options):
        for name, lessons, validity, price in [
            ("Первый пробный урок", 1, 7, 1000),
            ("Одно занятие", 1, 7, 1600),
            ("Абонемент на 4 занятия", 4, 30, 5500),
        ]:
            PassPlan.objects.update_or_create(name=name, defaults={
                "public": True,
                "lessons": lessons, "validity_days": validity, "price": price,
            })
        PassPlan.objects.filter(name="Абонемент на 8 занятий", public=True).update(public=False)
        self.stdout.write("Демонстрационные тарифы доступны родителям.")
