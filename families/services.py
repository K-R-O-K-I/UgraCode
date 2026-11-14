import secrets
from dataclasses import dataclass
from datetime import timedelta
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone
from dashboard import services as master
from dashboard.models import (
    Booking, Child, Family, Invoice, Lesson, LessonEntry, Notification,
    PassPlan, Payment, Subscription,
)
from .models import AccessLink, Checkout
from .security import token_hash


@dataclass
class ParentActor:
    family: Family

    @property
    def username(self):
        return f"family-{self.family.pk}"

    def get_full_name(self):
        return f"Родитель · {self.family.contact_name}"


@transaction.atomic
def issue_link(family):
    Family.objects.select_for_update().get(pk=family.pk, active=True)
    AccessLink.objects.filter(family=family, revoked_at__isnull=True).update(revoked_at=timezone.now())
    token = secrets.token_urlsafe(32)
    AccessLink.objects.create(family=family, token_hash=token_hash(token), expires_at=timezone.now()+timedelta(days=90))
    return token


@transaction.atomic
def revoke_links(family):
    Family.objects.select_for_update().get(pk=family.pk, active=True)
    AccessLink.objects.filter(family=family, revoked_at__isnull=True).update(revoked_at=timezone.now())


@transaction.atomic
def add_child(family, data):
    Family.objects.select_for_update().get(pk=family.pk, active=True)
    child = Child.objects.create(family=family, **{key: data[key] for key in ("name", "age", "description", "avatar") if key in data})
    master.audit(ParentActor(family), f"В семью добавлен ребёнок {child.name}")
    Notification.objects.create(text=f"{family.contact_name} добавил(а) ребёнка {child.name}")
    return child


@transaction.atomic
def parent_cancel(family, booking, reason):
    lesson = Lesson.objects.select_for_update().get(pk=booking.lesson_id)
    child = Child.objects.select_for_update().get(pk=booking.child_id, family=family)
    booking = Booking.objects.select_for_update().get(pk=booking.pk, child=child)
    if booking.status == "cancelled":
        return
    if lesson.starts_at <= timezone.now() or booking.attendance != "pending":
        raise ValidationError("Для прошедшего занятия можно сообщить причину отсутствия. Отметку изменяет мастер.")
    late = lesson.starts_at-timezone.now() < timedelta(hours=lesson.policy.free_cancel_hours)
    charge = late and lesson.policy.charge_late_cancel
    if charge:
        day = timezone.localdate(lesson.starts_at)
        subscriptions = Subscription.objects.select_for_update().filter(
            invoice__child=child, starts_on__lte=day, expires_on__gte=day
        ).order_by("expires_on", "pk")
        subscription = next((item for item in subscriptions if item.balance > 0), None)
        if subscription:
            LessonEntry.objects.create(subscription=subscription, booking=booking, delta=-1, reason="Поздняя отмена родителем")
        else:
            booking.cancellation_charge_pending = True
    booking.status = "cancelled"
    booking.attendance = "cancelled"
    booking.parent_reason = reason
    booking.save(update_fields=["status", "attendance", "parent_reason", "cancellation_charge_pending"])
    master.audit(ParentActor(family), f"{child.name} — запись отменена родителем", reason=reason)
    Notification.objects.create(text=f"{child.name} · отмена «{lesson.title}». Причина — {reason}")
    master.family_notice(family.pk, f"Запись на «{lesson.title}» для {child.name} отменена.")


@transaction.atomic
def absence_reason(family, booking, reason):
    booking = Booking.objects.select_for_update().get(pk=booking.pk, child__family=family)
    before = booking.parent_reason
    booking.parent_reason = reason
    booking.save(update_fields=["parent_reason"])
    master.audit(ParentActor(family), f"{booking.child.name} — комментарий родителя",
                 before={"reason": before}, after={"reason": reason})
    Notification.objects.create(text=f"{booking.child.name} · {booking.lesson.title}. Родитель — {reason}")


@transaction.atomic
def create_checkout(family, child, plan, key, provider):
    Family.objects.select_for_update().get(pk=family.pk, active=True)
    if child.family_id != family.pk or not child.family.active or not plan.public:
        raise ValidationError("Проверьте ребёнка и выбранный абонемент.")
    old = Checkout.objects.filter(pk=key).select_related("invoice").first()
    if old:
        if old.invoice.child_id != child.pk or old.invoice.plan_id != plan.pk:
            raise ValidationError("Операция уже существует с другими параметрами.")
        return old
    pending = Checkout.objects.filter(invoice__child=child, invoice__plan=plan, status="pending", provider=provider).first()
    if pending:
        return pending
    invoice = Invoice.objects.create(child=child, plan=plan, amount=plan.price,
                                     lessons=plan.lessons, validity_days=plan.validity_days, status="open")
    return Checkout.objects.create(id=key, invoice=invoice, provider=provider)


@transaction.atomic
def create_booking_checkout(family, child, lesson):
    Family.objects.select_for_update().get(pk=family.pk, active=True)
    lesson = Lesson.objects.select_for_update().get(pk=lesson.pk)
    if child.family_id != family.pk or not child.family.active:
        raise ValidationError("Выберите ребёнка из своей семьи.")
    if lesson.status != "scheduled" or lesson.starts_at <= timezone.now():
        raise ValidationError("Запись на занятие закрыта.")
    if lesson.active_bookings.filter(child=child).exists():
        raise ValidationError("Ребёнок уже записан на это занятие.")
    if lesson.remaining <= 0:
        raise ValidationError("Свободных мест больше нет.")
    pending = Checkout.objects.filter(
        lesson=lesson, invoice__child=child, status="pending", provider="demo",
    ).first()
    if pending:
        return pending
    plan = PassPlan.objects.filter(public=True, lessons=1).order_by("pk").first()
    if not plan:
        raise ValidationError("Тариф на одно занятие пока недоступен.")
    days_until_lesson = (timezone.localdate(lesson.starts_at) - timezone.localdate()).days
    invoice = Invoice.objects.create(
        child=child, plan=plan, amount=plan.price, lessons=1,
        validity_days=max(plan.validity_days, days_until_lesson + 1), status="open",
    )
    return Checkout.objects.create(invoice=invoice, lesson=lesson, provider="demo")


@transaction.atomic
def complete(checkout_id):
    checkout = Checkout.objects.select_for_update().select_related("invoice__child").get(pk=checkout_id)
    invoice = Invoice.objects.select_for_update().get(pk=checkout.invoice_id)
    if checkout.status == "succeeded":
        return
    if checkout.status != "pending" or invoice.status != "open":
        raise ValidationError("Этот платёж уже отменён.")
    child = invoice.child
    if checkout.lesson_id:
        master.book(ParentActor(child.family), {"lesson": checkout.lesson, "child": child})
    Payment.objects.create(invoice=invoice, method=checkout.provider, amount=invoice.amount, operation_key=checkout.pk)
    day = timezone.localdate()
    subscription = Subscription.objects.create(invoice=invoice, starts_on=day, expires_on=day+timedelta(days=invoice.validity_days-1))
    LessonEntry.objects.create(subscription=subscription, delta=invoice.lessons, reason="Оплачен абонемент")
    invoice.status = "paid"
    invoice.save(update_fields=["status"])
    checkout.status = "succeeded"
    checkout.save(update_fields=["status"])
    child = checkout.invoice.child
    master.audit(ParentActor(child.family), f"{child.name} — оплачено {invoice.amount} ₽")
    master.family_notice(child.family_id, f"{child.name} · оплата подтверждена, начислено {invoice.lessons} занятий.")


@transaction.atomic
def decline(checkout_id):
    checkout = Checkout.objects.select_for_update().get(pk=checkout_id)
    if checkout.status != "pending":
        return
    invoice = Invoice.objects.select_for_update().get(pk=checkout.invoice_id)
    checkout.status = "cancelled"
    invoice.status = "cancelled"
    checkout.save(update_fields=["status"])
    invoice.save(update_fields=["status"])
