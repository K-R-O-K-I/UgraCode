from pathlib import Path
import uuid
from django.db import models
from django.db.models import Q, F, Sum
from django.utils import timezone


class Family(models.Model):
    contact_name = models.CharField(max_length=160)
    phone = models.CharField(max_length=32, blank=True)
    def __str__(self):
        return self.contact_name


class Child(models.Model):
    family = models.ForeignKey(Family, on_delete=models.PROTECT, related_name="children")
    name = models.CharField(max_length=160)
    active = models.BooleanField(default=True)
    def __str__(self):
        return self.name
    @property
    def balance(self):
        return LessonEntry.objects.filter(subscription__invoice__child=self).aggregate(total=Sum("delta"))["total"] or 0
    @property
    def initials(self):
        return "".join(part[0] for part in self.name.split()[:2])


class LessonPolicy(models.Model):
    free_cancel_hours = models.PositiveIntegerField(default=12)
    charge_absence = models.BooleanField(default=True)
    charge_late_cancel = models.BooleanField(default=False)


class Lesson(models.Model):
    KIND = [("group", "Групповое"), ("individual", "Индивидуальное")]
    policy = models.ForeignKey(LessonPolicy, on_delete=models.PROTECT)
    title = models.CharField(max_length=160)
    kind = models.CharField(max_length=16, choices=KIND, default="group")
    starts_at = models.DateTimeField()
    ends_at = models.DateTimeField()
    capacity = models.PositiveIntegerField(default=6)
    status = models.CharField(max_length=16, default="scheduled")
    description = models.TextField(blank=True)
    image = models.CharField(max_length=64, default="wooden-toy.jpg")
    class Meta:
        ordering = ["starts_at", "pk"]
        constraints = [
            models.CheckConstraint(condition=Q(ends_at__gt=F("starts_at")), name="lesson_end_after_start"),
            models.CheckConstraint(condition=Q(capacity__gt=0), name="positive_capacity"),
            models.CheckConstraint(condition=~Q(kind="individual") | Q(capacity=1), name="individual_capacity"),
        ]
    def __str__(self):
        return f"{self.title} · {timezone.localtime(self.starts_at):%d.%m %H:%M}"
    @property
    def active_bookings(self):
        return self.bookings.filter(status="booked")
    @property
    def remaining(self):
        return self.capacity - self.active_bookings.count()


class Booking(models.Model):
    ATTENDANCE = [("pending", "Без отметки"), ("present", "Посетил"), ("absent", "Пропустил"), ("cancelled", "Отмена")]
    lesson = models.ForeignKey(Lesson, on_delete=models.PROTECT, related_name="bookings")
    child = models.ForeignKey(Child, on_delete=models.PROTECT)
    status = models.CharField(max_length=16, default="booked")
    attendance = models.CharField(max_length=16, choices=ATTENDANCE, default="pending")
    created_at = models.DateTimeField(auto_now_add=True)
    class Meta:
        ordering = ["pk"]
        constraints = [models.UniqueConstraint(fields=["lesson", "child"], name="unique_child_lesson")]


class PassPlan(models.Model):
    name = models.CharField(max_length=120)
    lessons = models.PositiveIntegerField()
    validity_days = models.PositiveIntegerField()
    price = models.DecimalField(max_digits=12, decimal_places=2)


class Invoice(models.Model):
    child = models.ForeignKey(Child, on_delete=models.PROTECT)
    plan = models.ForeignKey(PassPlan, on_delete=models.PROTECT)
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    lessons = models.PositiveIntegerField()
    validity_days = models.PositiveIntegerField()
    status = models.CharField(max_length=16, default="paid")


class Payment(models.Model):
    invoice = models.ForeignKey(Invoice, on_delete=models.PROTECT)
    method = models.CharField(max_length=16, choices=[("cash", "Наличные"), ("transfer", "Перевод")])
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    operation_key = models.UUIDField(unique=True, default=uuid.uuid4)
    confirmed_at = models.DateTimeField(default=timezone.now)
    class Meta:
        ordering = ["-confirmed_at"]


class Subscription(models.Model):
    invoice = models.OneToOneField(Invoice, on_delete=models.PROTECT)
    starts_on = models.DateField()
    expires_on = models.DateField()
    @property
    def balance(self):
        return self.entries.aggregate(total=Sum("delta"))["total"] or 0


class LessonEntry(models.Model):
    subscription = models.ForeignKey(Subscription, on_delete=models.PROTECT, related_name="entries")
    booking = models.ForeignKey(Booking, null=True, blank=True, on_delete=models.PROTECT)
    delta = models.IntegerField()
    reason = models.CharField(max_length=240)
    operation_key = models.UUIDField(unique=True, default=uuid.uuid4)
    reverses = models.OneToOneField("self", null=True, blank=True, on_delete=models.PROTECT, related_name="reversal")
    created_at = models.DateTimeField(auto_now_add=True)


def receipt_path(instance, filename):
    return f"{uuid.uuid4().hex}{Path(filename).suffix.lower()}"


class Receipt(models.Model):
    payment = models.ForeignKey(Payment, on_delete=models.PROTECT, related_name="receipts")
    file = models.FileField(upload_to=receipt_path, blank=True)
    external_url = models.URLField(max_length=1000, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)


class AuditEvent(models.Model):
    actor_label = models.CharField(max_length=160)
    text = models.CharField(max_length=500)
    before_data = models.JSONField(default=dict)
    after_data = models.JSONField(default=dict)
    reason = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    class Meta:
        ordering = ["-created_at"]


class Notification(models.Model):
    text = models.CharField(max_length=500)
    read_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    class Meta:
        ordering = ["-created_at"]
