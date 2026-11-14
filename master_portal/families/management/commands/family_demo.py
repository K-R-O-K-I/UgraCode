from django.core.management.base import BaseCommand
from dashboard.models import PassPlan


class Command(BaseCommand):
    help = "Добавить публичные демонстрационные тарифы."

    def handle(self, *args, **options):
        for name, lessons, validity, price in [
            ("Одно занятие", 1, 7, 1400),
            ("Абонемент на 4 занятия", 4, 30, 4800),
            ("Абонемент на 8 занятий", 8, 60, 8800),
        ]:
            PassPlan.objects.get_or_create(name=name, public=True, defaults={
                "lessons": lessons, "validity_days": validity, "price": price,
            })
        self.stdout.write("Демонстрационные тарифы доступны родителям.")
