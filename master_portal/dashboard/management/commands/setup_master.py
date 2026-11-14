from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Создать учётную запись мастера при первом локальном запуске."
    def handle(self, *args, **options):
        if get_user_model().objects.filter(is_staff=True, is_active=True).exists():
            self.stdout.write("Учётная запись мастера уже создана.")
            return
        self.stdout.write("Создайте логин и пароль для входа в кабинет.")
        call_command("createsuperuser")
