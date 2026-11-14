import re
from django import forms
from .models import Family

class FamilyForm(forms.ModelForm):
    class Meta:
        model = Family
        fields = ("contact_name", "phone")
        widgets = {
            "contact_name": forms.TextInput(attrs={"placeholder": "Семья Ивановых"}),
            "phone": forms.TextInput(attrs={"type": "tel", "placeholder": "8 (922) 124 1235", "inputmode": "tel", "maxlength": "16", "data-phone": "", "pattern": r"8 \(\d{3}\) \d{3} \d{4}"}),
        }

    def clean_phone(self):
        value = self.cleaned_data["phone"]
        if re.search(r"[^0-9+() \-]", value):
            raise forms.ValidationError("Введите телефон: 8 (922) 124 1235.")
        digits = re.sub(r"\D", "", value)
        if len(digits) != 11 or digits[0] not in "78":
            raise forms.ValidationError("Номер должен содержать 11 цифр и начинаться с 8 или +7.")
        return f"8 ({digits[1:4]}) {digits[4:7]} {digits[7:]}"
