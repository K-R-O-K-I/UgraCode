from datetime import timedelta
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone
from .models import (
    AuditEvent, Booking, Child, Family, Invoice, Lesson, LessonEntry,
    LessonPolicy, Notification, PassPlan, Payment, Receipt, Subscription,
)


def audit(actor, text, before=None, after=None, reason=""):
    AuditEvent.objects.create(
        actor_label=actor.get_full_name() or actor.username,
        text=text, before_data=before or {}, after_data=after or {}, reason=reason,
    )


def family_notice(family_id, text):
    Notification.objects.create(family_id=family_id, text=text)


def lesson_notice(lesson, text):
    family_ids = lesson.bookings.values_list("child__family_id", flat=True).distinct()
    for family_id in family_ids:
        family_notice(family_id, text)


def restore(booking):
    charges = LessonEntry.objects.filter(booking=booking, delta__lt=0, reversal__isnull=True)
    for charge in charges:
        LessonEntry.objects.create(
            subscription=charge.subscription, booking=booking, delta=-charge.delta,
            reverses=charge, reason="Возврат списанного занятия",
        )


@transaction.atomic
def save_lesson(actor, data, pk=None):
    lesson = Lesson.objects.select_for_update().get(pk=pk) if pk else Lesson()
    if pk and lesson.status != "scheduled":
        raise ValidationError("Отменённое занятие нельзя редактировать.")
    if pk and lesson.active_bookings.count() > data["capacity"]:
        raise ValidationError("Вместимость меньше числа записанных детей.")
    for key, value in data.items():
        setattr(lesson, key, value)
    if not pk:
        lesson.policy = LessonPolicy.objects.order_by("-pk").first()
        if not lesson.policy:
            lesson.policy = LessonPolicy.objects.create()
    lesson.image = "wood-turning.jpg" if lesson.kind == "individual" else "wooden-toy.jpg"
    lesson.full_clean()
    lesson.save()
    audit(actor, f"Сохранено занятие «{lesson.title}»")
    Notification.objects.create(text=f"Обновлено расписание · {lesson.title}")
    if pk:
        lesson_notice(lesson, f"Изменено занятие «{lesson.title}». Проверьте расписание.")
    return lesson


def book_locked(actor, lesson, child):
    if lesson.status != "scheduled" or lesson.starts_at <= timezone.now():
        raise ValidationError("Занятие отменено или уже завершилось.")
    if not child.active:
        raise ValidationError("Ученик неактивен.")
    old = Booking.objects.filter(lesson=lesson, child=child).first()
    if old and old.status == "booked":
        raise ValidationError("Ребёнок уже записан на это занятие.")
    if lesson.remaining <= 0:
        raise ValidationError("Свободных мест больше нет.")
    if old:
        restore(old)
        old.status, old.attendance = "booked", "pending"
        old.parent_reason = ""
        old.cancellation_charge_pending = False
        old.save(update_fields=["status", "attendance", "parent_reason", "cancellation_charge_pending"])
        booking = old
    else:
        booking = Booking.objects.create(lesson=lesson, child=child)
    audit(actor, f"{child.name} — запись на «{lesson.title}»")
    Notification.objects.create(text=f"{child.name} записан на «{lesson.title}»")
    family_notice(child.family_id, f"{child.name} записан на «{lesson.title}».")
    return booking


@transaction.atomic
def book(actor, data):
    lesson = Lesson.objects.select_for_update().get(pk=data["lesson"].pk)
    child = Child.objects.select_for_update().get(pk=data["child"].pk)
    return book_locked(actor, lesson, child)


@transaction.atomic
def mark(actor, data):
    source = data["booking"]
    lesson = Lesson.objects.select_for_update().get(pk=source.lesson_id)
    Child.objects.select_for_update().get(pk=source.child_id)
    booking = Booking.objects.select_for_update().get(pk=source.pk)
    attendance, reason = data["attendance"], data.get("reason", "")
    if lesson.status != "scheduled" or booking.status != "booked":
        raise ValidationError("Занятие или запись отменены. Для восстановления запишите ребёнка повторно.")
    if booking.attendance == attendance:
        return
    if booking.attendance != "pending" and not reason.strip():
        raise ValidationError("Укажите причину исправления отметки.")
    policy = lesson.policy
    late = lesson.starts_at - timezone.now() < timedelta(hours=policy.free_cancel_hours)
    charge = (
        attendance == "present"
        or (attendance == "absent" and policy.charge_absence)
        or (attendance == "cancelled" and late and policy.charge_late_cancel)
    )
    restore(booking)
    if charge:
        day = timezone.localdate(lesson.starts_at)
        subscriptions = Subscription.objects.select_for_update().filter(
            invoice__child_id=booking.child_id, starts_on__lte=day, expires_on__gte=day,
        ).order_by("expires_on", "pk")
        subscription = next((item for item in subscriptions if item.balance > 0), None)
        if not subscription:
            raise ValidationError("Нет действующего абонемента с остатком. Внесите оплату за абонемент или разовое занятие.")
        LessonEntry.objects.create(
            subscription=subscription, booking=booking, delta=-1,
            reason="Посещение" if attendance == "present" else "Списание по правилам",
        )
    before = {"attendance": booking.attendance}
    booking.attendance = attendance
    if attendance == "cancelled":
        booking.status = "cancelled"
    booking.save(update_fields=["attendance", "status"])
    audit(actor, f"{booking.child.name} — {booking.get_attendance_display()}",
          before, {"attendance": attendance}, reason)
    family_notice(booking.child.family_id, f"{booking.child.name} · {lesson.title} · {booking.get_attendance_display()}")


@transaction.atomic
def cancel(actor, data):
    lesson = Lesson.objects.select_for_update().get(pk=data["lesson"].pk)
    if lesson.status == "cancelled":
        return
    bookings = list(lesson.bookings.order_by("pk"))
    list(Child.objects.select_for_update().filter(pk__in=[item.child_id for item in bookings]).order_by("pk"))
    for booking in bookings:
        restore(booking)
        booking.status, booking.attendance = "cancelled", "cancelled"
        booking.cancellation_charge_pending = False
        booking.save(update_fields=["status", "attendance", "cancellation_charge_pending"])
    lesson_notice(lesson, f"Занятие «{lesson.title}» отменено мастером. Причина — {data['reason']}")
    lesson.status = "cancelled"
    lesson.save(update_fields=["status"])
    audit(actor, f"Отменено занятие «{lesson.title}»", reason=data["reason"])
    Notification.objects.create(text=f"Отменено занятие «{lesson.title}». Списанные занятия возвращены.")


@transaction.atomic
def move(actor, data):
    source = data["booking"]
    target = data["lesson"]
    lessons = {item.pk: item for item in Lesson.objects.select_for_update().filter(pk__in=[source.lesson_id, target.pk]).order_by("pk")}
    child = Child.objects.select_for_update().get(pk=source.child_id)
    booking = Booking.objects.select_for_update().get(pk=source.pk)
    if booking.lesson_id == target.pk:
        raise ValidationError("Выберите другое занятие.")
    if booking.status != "booked" or booking.attendance != "pending":
        raise ValidationError("Перенос доступен для активной записи без отметки.")
    book_locked(actor, lessons[target.pk], child)
    booking.status, booking.attendance = "cancelled", "cancelled"
    booking.save(update_fields=["status", "attendance"])
    audit(actor, f"{child.name} — запись перенесена", reason=data["reason"])


@transaction.atomic
def add_child(actor, data):
    family = data.get("family") or Family.objects.create(contact_name=data["parent"])
    child = Child.objects.create(family=family, name=data["name"])
    audit(actor, f"Добавлен ученик {child.name}")
    return child


@transaction.atomic
def pay(actor, data):
    child = Child.objects.select_for_update().get(pk=data["child"].pk)
    existing = Payment.objects.filter(operation_key=data["operation_key"]).first()
    if existing:
        if (
            existing.invoice.child_id != child.pk or existing.amount != data["amount"]
            or existing.invoice.lessons != data["lessons"]
            or existing.invoice.validity_days != data["validity_days"]
            or existing.method != data["method"]
        ):
            raise ValidationError("Идентификатор операции уже использован.")
        return existing
    plan = PassPlan.objects.create(
        name=f'Абонемент на {data["lessons"]} зан.', lessons=data["lessons"],
        validity_days=data["validity_days"], price=data["amount"],
    )
    invoice = Invoice.objects.create(
        child=child, plan=plan, amount=plan.price,
        lessons=plan.lessons, validity_days=plan.validity_days,
    )
    payment = Payment.objects.create(invoice=invoice, amount=plan.price, method=data["method"], operation_key=data["operation_key"])
    today = timezone.localdate()
    subscription = Subscription.objects.create(invoice=invoice, starts_on=today, expires_on=today + timedelta(days=plan.validity_days - 1))
    LessonEntry.objects.create(subscription=subscription, delta=plan.lessons, reason="Оплачен абонемент")
    audit(actor, f"{child.name} — оплата {payment.amount} ₽, начислено {plan.lessons} зан.")
    family_notice(child.family_id, f"Получена оплата за {child.name}. Начислено {plan.lessons} занятий.")
    return payment


def attach_receipt(actor, data):
    receipt = Receipt.objects.create(
        payment=data["payment"], external_url=data.get("external_url", ""),
        file=data.get("file") or "",
    )
    audit(actor, "Прикреплён чек", after={"payment": receipt.payment_id})
    family_notice(receipt.payment.invoice.child.family_id, "Мастер прикрепил чек к оплате. Он доступен в разделе «Оплаты».")
    return receipt


@transaction.atomic
def policy(actor, data):
    result = LessonPolicy.objects.create(**data)
    audit(actor, "Созданы правила для новых занятий")
    return result
