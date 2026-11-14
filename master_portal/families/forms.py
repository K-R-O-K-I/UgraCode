import uuid
from io import BytesIO
from PIL import Image, ImageOps
from django import forms
from django.utils import timezone
from dashboard.models import Child, Family, Lesson, PassPlan


class FamilyForm(forms.ModelForm):
    class Meta:
        model = Family
        fields = ["contact_name", "phone"]
        labels = {"contact_name": "Имя и фамилия родителя", "phone": "Телефон"}


class ChildForm(forms.ModelForm):
    age = forms.IntegerField(label="Возраст", min_value=0, max_value=120, required=False)
    avatar_upload = forms.FileField(
        label="Аватарка", required=False,
        help_text="JPEG, PNG или WebP, до 5 МБ.",
        widget=forms.ClearableFileInput(attrs={"accept": "image/jpeg,image/png,image/webp"}),
    )

    class Meta:
        model = Child
        fields = ["name", "age", "description", "avatar_upload"]
        labels = {"name": "Имя и фамилия ребёнка", "description": "Описание"}
        widgets = {"description": forms.Textarea(attrs={"rows": 3})}

    def clean_avatar_upload(self):
        upload = self.cleaned_data["avatar_upload"]
        if not upload:
            return None
        if upload.size > 5 * 1024 * 1024:
            raise forms.ValidationError("Размер изображения должен быть до 5 МБ.")
        try:
            with Image.open(upload, formats=["JPEG", "PNG", "WEBP"]) as image:
                if image.width * image.height > 16_000_000:
                    raise forms.ValidationError("Изображение слишком большое.")
                avatar = ImageOps.exif_transpose(image).convert("RGB")
                avatar.thumbnail((256, 256))
                output = BytesIO()
                avatar.save(output, format="JPEG", quality=85)
                return output.getvalue()
        except (OSError, ValueError, Image.DecompressionBombError) as error:
            raise forms.ValidationError("Выберите корректное изображение JPEG, PNG или WebP.") from error


class BookForm(forms.Form):
    child = forms.ModelChoiceField(label="Ребёнок", queryset=Child.objects.none())
    lesson = forms.ModelChoiceField(label="Занятие", queryset=Lesson.objects.none())

    def __init__(self, *args, family, lesson=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["child"].queryset = family.children.filter(active=True)
        lessons = Lesson.objects.filter(status="scheduled", starts_at__gt=timezone.now())
        if lesson:
            lessons = lessons.filter(pk=lesson.pk)
            self.fields["lesson"].widget = forms.HiddenInput()
        self.fields["lesson"].queryset = lessons


class ReasonForm(forms.Form):
    reason = forms.CharField(label="Причина отсутствия", max_length=500, widget=forms.Textarea(attrs={"rows": 3}))


class BuyForm(forms.Form):
    child = forms.ModelChoiceField(label="Ребёнок", queryset=Child.objects.none())
    plan = forms.ModelChoiceField(label="Абонемент", queryset=PassPlan.objects.filter(public=True))
    key = forms.UUIDField(initial=uuid.uuid4, widget=forms.HiddenInput)

    def __init__(self, *args, family, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["child"].queryset = family.children.filter(active=True)
