import uuid
from django import forms
from django.utils import timezone
from dashboard.models import Child, Family, Lesson, PassPlan


class ChildForm(forms.ModelForm):
    class Meta:
        model = Child
        fields = ("name", "age", "description", "avatar")
        widgets = {
            "name": forms.TextInput(attrs={"placeholder": "Миша Иванов"}),
            "age": forms.NumberInput(attrs={"placeholder": "8", "min": 0, "max": 100}),
            "description": forms.Textarea(attrs={"placeholder": "Интересы и особенности ребёнка, которые стоит знать мастеру", "rows": 3}),
        }

    def clean_avatar(self):
        avatar = self.cleaned_data.get("avatar")
        if avatar and hasattr(avatar, "content_type"):
            if avatar.size > 5 * 1024 * 1024:
                raise forms.ValidationError("Изображение должно быть не больше 5 МБ.")
            if avatar.image.format not in ("JPEG", "PNG", "WEBP"):
                raise forms.ValidationError("Выберите изображение JPEG, PNG или WebP.")
        return avatar


class BookForm(forms.Form):
    child = forms.ModelChoiceField(label="Ребёнок", queryset=Child.objects.none())
    lesson = forms.ModelChoiceField(label="Занятие", queryset=Lesson.objects.none())

    def __init__(self, *args, family, lesson=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["child"].queryset = family.children.all()
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
        self.fields["child"].queryset = family.children.all()
