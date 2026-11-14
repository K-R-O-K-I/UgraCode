from datetime import datetime, time, timedelta
from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone
from dashboard.models import Booking, Child, Family, Invoice, Lesson, LessonEntry, LessonPolicy, PassPlan, Subscription, Notification


class Command(BaseCommand):
    help = "Создать вымышленные данные для демонстрации кабинета."

    @transaction.atomic
    def handle(self, *args, **options):
        if Lesson.objects.exists():
            self.stdout.write("Демоданные уже есть.")
            return
        policy = LessonPolicy.objects.create()
        today = timezone.localdate()
        names = [
            ("Миша Соколов", "Анна Соколова"), ("Соня Соколова", "Анна Соколова"),
            ("Артём Волков", "Мария Волкова"), ("Алиса Морозова", "Ирина Морозова"),
            ("Лёва Орлов", "Алексей Орлов"), ("Варя Кузнецова", "Ольга Кузнецова"),
        ]
        children = []
        plan = PassPlan.objects.create(name="Демоабонемент", lessons=4, validity_days=30, price=4800)
        for index, (name, parent) in enumerate(names):
            family, _ = Family.objects.get_or_create(contact_name=parent)
            child = Child.objects.create(family=family, name=name)
            children.append(child)
            invoice = Invoice.objects.create(child=child, plan=plan, amount=4800, lessons=4, validity_days=30)
            subscription = Subscription.objects.create(invoice=invoice, starts_on=today, expires_on=today + timedelta(days=29))
            LessonEntry.objects.create(subscription=subscription, delta=1 if index == 2 else 4, reason="Начальный остаток демонстрации")
        specs = [
            ("Деревянные истории", 10, 6, "wooden-toy.jpg", 0),
            ("Мастерим своими руками", 12, 4, "wooden-craft.jpg", 0),
            ("Знакомство с токарным делом", 15, 1, "wood-turning.jpg", 0),
            ("Первые шаги в мастерской", 11, 6, "wooden-toy.jpg", 1),
        ]
        lessons = []
        for title, hour, capacity, image, offset in specs:
            start = timezone.make_aware(datetime.combine(today + timedelta(days=offset), time(hour)))
            lessons.append(Lesson.objects.create(
                policy=policy, title=title, starts_at=start, ends_at=start+timedelta(minutes=90),
                capacity=capacity, kind="individual" if capacity==1 else "group", image=image,
                description="Собираем деревянную игрушку. Подготовить заготовки, наждачную бумагу и столярный клей.",
            ))
        for lesson_index, child_index in [(0,0),(0,1),(0,2),(0,3),(1,2),(1,4),(1,5),(2,4)]:
            Booking.objects.create(lesson=lessons[lesson_index], child=children[child_index])
        Notification.objects.create(text="Артём Волков · осталось одно занятие. Можно предложить продление абонемента.")
        self.stdout.write(self.style.SUCCESS("Созданы вымышленные семьи, занятия и абонементы."))
