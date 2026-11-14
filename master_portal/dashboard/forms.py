import uuid
from datetime import timedelta
from decimal import Decimal
from pathlib import Path
from django import forms
from django.core.exceptions import ValidationError
from django.utils import timezone
from PIL import Image
from .models import Booking, Child, Family, Lesson, Payment


class LessonForm(forms.Form):
    title = forms.CharField(label="Название занятия", max_length=160)
    starts_at = forms.DateTimeField(label="Начало", widget=forms.DateTimeInput(attrs={"type": "datetime-local"}, format="%Y-%m-%dT%H:%M"))
    ends_at = forms.DateTimeField(label="Окончание", widget=forms.DateTimeInput(attrs={"type": "datetime-local"}, format="%Y-%m-%dT%H:%M"))
    kind = forms.ChoiceField(label="Формат", choices=Lesson.KIND)
    capacity = forms.IntegerField(label="Количество мест", min_value=1, max_value=50, initial=6)
    description = forms.CharField(label="Задание и материалы", required=False, max_length=2000, widget=forms.Textarea(attrs={"rows": 3}))
    def clean(self):
        data = super().clean()
        if data.get("ends_at") and data.get("starts_at") and data["ends_at"] <= data["starts_at"]:
            raise ValidationError("Окончание должно быть позже начала.")
        if data.get("kind") == "individual" and data.get("capacity") != 1:
            raise ValidationError("Для индивидуального занятия укажите одно место.")
        return data


class BookingForm(forms.Form):
    lesson = forms.ModelChoiceField(label="Занятие", queryset=Lesson.objects.filter(status="scheduled", starts_at__gt=timezone.now()))
    child = forms.ModelChoiceField(label="Ученик", queryset=Child.objects.filter(active=True))
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["lesson"].queryset = Lesson.objects.filter(status="scheduled", starts_at__gt=timezone.now())


class AttendanceForm(forms.Form):
    booking = forms.ModelChoiceField(queryset=Booking.objects.all(), widget=forms.HiddenInput)
    attendance = forms.ChoiceField(label="Отметка", choices=Booking.ATTENDANCE)
    reason = forms.CharField(label="Причина или комментарий", required=False, max_length=500, widget=forms.Textarea(attrs={"rows": 3}))


class CancelForm(forms.Form):
    lesson = forms.ModelChoiceField(queryset=Lesson.objects.all(), widget=forms.HiddenInput)
    reason = forms.CharField(label="Причина отмены", max_length=500, widget=forms.Textarea(attrs={"rows": 3}))


class MoveForm(BookingForm):
    booking = forms.ModelChoiceField(queryset=Booking.objects.all(), widget=forms.HiddenInput)
    child = None
    reason = forms.CharField(label="Причина переноса", max_length=500)


class ChildForm(forms.Form):
    name = forms.CharField(label="Имя и фамилия ребёнка", max_length=160)
    family = forms.ModelChoiceField(label="Существующая семья", queryset=Family.objects.all(), required=False)
    parent = forms.CharField(label="Или имя родителя новой семьи", max_length=160, required=False)

    def clean(self):
        data = super().clean()
        if not data.get("family") and not data.get("parent"):
            raise ValidationError("Выберите семью или укажите имя родителя.")
        return data


class PaymentForm(forms.Form):
    child = forms.ModelChoiceField(label="Ученик", queryset=Child.objects.filter(active=True))
    amount = forms.DecimalField(label="Получено, ₽", max_digits=12, decimal_places=2, min_value=Decimal("0.01"), initial=4800)
    method = forms.ChoiceField(label="Способ оплаты", choices=[("cash", "Наличные"), ("transfer", "Перевод")])
    lessons = forms.IntegerField(label="Количество занятий", min_value=1, max_value=100, initial=4)
    validity_days = forms.IntegerField(label="Срок действия, дней", min_value=1, max_value=365, initial=30)
    operation_key = forms.UUIDField(widget=forms.HiddenInput, initial=uuid.uuid4)


class ReceiptForm(forms.Form):
    payment = forms.ModelChoiceField(queryset=Payment.objects.all(), widget=forms.HiddenInput)
    external_url = forms.URLField(label="Ссылка на чек", required=False, max_length=1000)
    file = forms.FileField(label="Или файл PDF, PNG, JPG до 2 МБ", required=False, widget=forms.FileInput(attrs={"accept": ".pdf,.png,.jpg,.jpeg"}))
    def clean(self):
        data = super().clean()
        uploaded, url = data.get("file"), data.get("external_url")
        if bool(uploaded) == bool(url):
            raise ValidationError("Добавьте один источник — файл или ссылку.")
        if url and not url.startswith("https://"):
            raise ValidationError("Ссылка должна начинаться с https://.")
        if uploaded:
            if uploaded.size > 2 * 1024 * 1024:
                raise ValidationError("Файл должен быть меньше 2 МБ.")
            extension = Path(uploaded.name).suffix.lower()
            if extension == ".pdf":
                if uploaded.read(5) != b"%PDF-":
                    raise ValidationError("Файл не является PDF.")
            elif extension in (".png", ".jpg", ".jpeg"):
                try:
                    image = Image.open(uploaded)
                    image.verify()
                    if image.format not in ("PNG", "JPEG"):
                        raise ValueError()
                except Exception as error:
                    raise ValidationError("Некорректное изображение.") from error
            else:
                raise ValidationError("Поддерживаются PDF, PNG и JPG.")
            uploaded.seek(0)
        return data


class PolicyForm(forms.Form):
    free_cancel_hours = forms.IntegerField(label="Бесплатная отмена, часов до начала", min_value=0, max_value=168, initial=12)
    charge_absence = forms.BooleanField(label="Списывать занятие при пропуске", required=False, initial=True)
    charge_late_cancel = forms.BooleanField(label="Списывать занятие при поздней отмене", required=False)
