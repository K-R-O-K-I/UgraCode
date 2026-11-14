import uuid
from django import forms
from django.utils import timezone
from dashboard.models import Child, Family, Lesson, PassPlan


class FamilyForm(forms.ModelForm):
    class Meta:
        model = Family
        fields = ["contact_name", "phone"]
        labels = {"contact_name": "Имя и фамилия родителя", "phone": "Телефон"}


class ChildForm(forms.Form):
    name = forms.CharField(label="Имя и фамилия ребёнка", max_length=160)


class BookForm(forms.Form):
    child = forms.ModelChoiceField(label="Ребёнок", queryset=Child.objects.none())
    lesson = forms.ModelChoiceField(label="Занятие", queryset=Lesson.objects.none())

    def __init__(self, *args, family, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["child"].queryset = family.children.filter(active=True)
        self.fields["lesson"].queryset = Lesson.objects.filter(status="scheduled", starts_at__gt=timezone.now())


class ReasonForm(forms.Form):
    reason = forms.CharField(label="Причина отсутствия", max_length=500, widget=forms.Textarea(attrs={"rows": 3}))


class BuyForm(forms.Form):
    child = forms.ModelChoiceField(label="Ребёнок", queryset=Child.objects.none())
    plan = forms.ModelChoiceField(label="Абонемент", queryset=PassPlan.objects.filter(public=True))
    key = forms.UUIDField(initial=uuid.uuid4, widget=forms.HiddenInput)

    def __init__(self, *args, family, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["child"].queryset = family.children.filter(active=True)
