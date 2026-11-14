from django.db import models
from django.core.validators import MaxValueValidator
import uuid
from pathlib import Path

def avatar_path(instance, filename):
    return f"avatars/{uuid.uuid4().hex}{Path(filename).suffix.lower()}"



class Family(models.Model):
    active = models.BooleanField("Активна", default=True)
    contact_name = models.CharField("Имя семьи", max_length=160)
    phone = models.CharField("Телефон", max_length=32)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "семья"
        verbose_name_plural = "семьи"

    def __str__(self):
        return self.contact_name


class Child(models.Model):
    family = models.ForeignKey(Family, on_delete=models.PROTECT, related_name="children")
    name = models.CharField("Имя ребёнка", max_length=160)
    age = models.PositiveSmallIntegerField("Возраст", null=True, blank=True, validators=[MaxValueValidator(100)])
    description = models.TextField("Описание", blank=True, max_length=2000)
    avatar = models.ImageField("Аватарка", upload_to=avatar_path, blank=True)

    class Meta:
        verbose_name = "ребёнок"
        verbose_name_plural = "дети"

    def __str__(self):
        return self.name


    @property
    def balance(self):
        from django.db.models import Sum
        from dashboard.models import LessonEntry
        return LessonEntry.objects.filter(subscription__invoice__child=self).aggregate(total=Sum("delta"))["total"] or 0

    @property
    def initials(self):
        return "".join(part[0] for part in self.name.split()[:2])


class AccessLink(models.Model):
    family = models.ForeignKey(Family, on_delete=models.PROTECT, related_name="access_links")
    token_hash = models.CharField(max_length=64, unique=True)
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField()
    revoked_at = models.DateTimeField(null=True, blank=True)
