import hashlib
from datetime import datetime, time, timedelta
from pathlib import Path
from django.conf import settings
from django.core.files.base import ContentFile
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone
from dashboard.models import Child, Invoice, Lesson, LessonPolicy, PassPlan, Subscription, LessonEntry
from families.models import PaymentDocument
from families.services import book_from_balance

SAMPLE = """ПРИМЕР ДЛЯ ДЕМОНСТРАЦИИ. НЕ ДЛЯ РЕАЛЬНЫХ ПЛАТЕЖЕЙ.
Согласие на обработку персональных данных

Я, родитель или законный представитель ребёнка, разрешаю мастерской обрабатывать сведения, которые я вношу в личный кабинет: имя и контактный телефон семьи, имя и возраст ребёнка, описание профиля, сведения о записях, посещениях и оплатах.
Цель: организация занятий, связь с семьёй, ведение записей и учёт оплат.
Аватарка и описание профиля заполняются добровольно. Не указывайте в описании сведения о здоровье и другие чувствительные данные.

Перед реальным использованием мастерская должна указать оператора данных, его адрес и контакты, перечень действий с данными, сроки хранения и действия согласия, порядок отзыва и иных получателей данных, если они есть. Затем необходимо согласовать окончательный текст с ответственным за обработку данных.
Этот пример показывает работу формы согласия и не является готовым юридическим документом.
"""


class Command(BaseCommand):
    help = "Дополнить локальную демонстрацию: профили, записи на сегодня/завтра и пример документа."

    @transaction.atomic
    def handle(self, *args, **options):
        if not settings.DEBUG:
            raise CommandError("Демонстрационные данные разрешены только при DEBUG=True.")
        if not PaymentDocument.objects.filter(title="Согласие на обработку данных — демонстрационный пример").exists():
            data = SAMPLE.encode("utf-8")
            document = PaymentDocument(title="Согласие на обработку данных — демонстрационный пример", sha256=hashlib.sha256(data).hexdigest())
            document.file.save("consent-example.txt", ContentFile(data), save=True)
        descriptions = ["Любит собирать модели машин. Учится работать с деревом и придумывает свои конструкции.", "Увлекается рисованием и украшением поделок. Хочет сделать кормушку для птиц.", "Интересуется механизмами, любит командные проекты и деревянные головоломки.", "Любит мастерить подарки. Аккуратно раскрашивает детали и помогает другим ребятам."]
        children = list(Child.objects.filter(family__active=True).order_by("pk"))
        for index, child in enumerate(children):
            if child.age is None:
                child.age = 7 + index % 6
            if not child.description:
                child.description = descriptions[index % len(descriptions)]
            if not child.avatar:
                hair = '<path d="M42 80 Q40 22 80 24 Q122 22 118 80 L105 60 Q75 65 57 49 Z" fill="#000000"/>' if index % 2 else '<path d="M43 64 Q39 18 80 22 Q123 20 117 65 L98 44 L80 52 L65 42 Z" fill="#000000"/>'
                avatar = f'<svg xmlns="http://www.w3.org/2000/svg" width="160" height="160" viewBox="0 0 160 160"><rect width="160" height="160" rx="22" fill="#FFBC0D"/><path d="M20 160 Q20 112 80 112 Q140 112 140 160" fill="#D90007"/><ellipse cx="80" cy="76" rx="37" ry="44" fill="#FFFFFF"/>{hair}<circle cx="66" cy="78" r="3" fill="#000000"/><circle cx="94" cy="78" r="3" fill="#000000"/><path d="M68 97 Q80 107 92 97" fill="none" stroke="#D90007" stroke-width="3" stroke-linecap="round"/></svg>'
                child.avatar.save(f"demo-{child.pk}.svg", ContentFile(avatar.encode()), save=False)
            child.save()
        today = timezone.localdate()
        policy, _ = LessonPolicy.objects.get_or_create(free_cancel_hours=12, charge_absence=True, charge_late_cancel=True)
        plan, _ = PassPlan.objects.get_or_create(name="Демонстрационный баланс", defaults={"lessons":8,"validity_days":30,"price":0,"public":False})
        created = 0
        for offset in (0,1):
            for slot, title in enumerate(("Сборка кормушки из готовых деталей", "Сборка деревянной игрушки")):
                start = timezone.make_aware(datetime.combine(today+timedelta(days=offset),time(14+slot*3)))
                lesson, _ = Lesson.objects.get_or_create(title=title, starts_at=start, defaults={"ends_at":start+timedelta(minutes=90),"policy":policy,"capacity":max(6,len(children)),"description":"Собираем изделие из готовых деревянных деталей, шлифуем края и украшаем работу. Материалы и инструменты выдаёт мастер. Отмена без потери посещения — за 12 часов.","image": ("bird-feeder.png", "wooden-car.png")[slot]})
                if start <= timezone.now():
                    continue
                for child in children[slot::2]:
                    if lesson.bookings.filter(child=child).exists():
                        continue
                    if not Subscription.objects.filter(invoice__child=child, invoice__plan=plan, expires_on__gte=today).exists():
                        invoice = Invoice.objects.create(child=child,plan=plan,amount=0,lessons=8,validity_days=30)
                        subscription = Subscription.objects.create(invoice=invoice,starts_on=today,expires_on=today+timedelta(days=29))
                        LessonEntry.objects.create(subscription=subscription,delta=8,reason="Демонстрационный баланс")
                    if book_from_balance(child.family,child,lesson):
                        created += 1
        self.stdout.write(f"Профилей: {len(children)}. Новых записей: {created}. Пример документа готов.")
