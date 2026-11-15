import hashlib
from pathlib import Path
from django import forms
from django.contrib import messages
from django.http import FileResponse, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from families.models import PaymentDocument
from families.security import family_only
from .views import staff_only, base_context


class DocumentForm(forms.ModelForm):
    class Meta:
        model = PaymentDocument
        fields = ["title", "file"]

    def clean_file(self):
        item = self.cleaned_data["file"]
        if item.size > 5 * 1024 * 1024:
            raise forms.ValidationError("Максимальный размер — 5 МБ.")
        suffix = Path(item.name).suffix.lower()
        data = item.read()
        item.seek(0)
        if suffix == ".pdf" and data.startswith(b"%PDF-"):
            return item
        if suffix == ".txt":
            try:
                data.decode("utf-8-sig")
                return item
            except UnicodeDecodeError:
                pass
        raise forms.ValidationError("Загрузите PDF или текстовый файл в UTF-8.")


@staff_only
def documents(request):
    form = DocumentForm(request.POST or None, request.FILES or None)
    if request.method == "POST":
        if request.POST.get("action") == "archive":
            item = get_object_or_404(PaymentDocument, pk=request.POST.get("document"))
            item.active = False
            item.save(update_fields=["active"])
            messages.success(request, "Документ снят с публикации. Старые согласия сохранены.")
            return redirect("documents")
        if form.is_valid():
            item = form.save(commit=False)
            item.sha256 = hashlib.sha256(form.cleaned_data["file"].read()).hexdigest()
            form.cleaned_data["file"].seek(0)
            item.save()
            messages.success(request, "Документ добавлен для новых оплат.")
            return redirect("documents")
    data = base_context(request, "documents")
    data.update(form=form, documents=PaymentDocument.objects.all())
    return render(request, "dashboard/documents.html", data)


def document_file(request, pk):
    if request.user.is_authenticated and request.user.is_staff:
        return serve_document(get_object_or_404(PaymentDocument, pk=pk))
    return family_document(request, pk)


@family_only
def family_document(request, pk):
    from django.db.models import Q
    item = get_object_or_404(PaymentDocument.objects.filter(
        Q(active=True) | Q(checkout__invoice__child__family=request.family)
    ).distinct(), pk=pk)
    return serve_document(item)


def serve_document(item):
    response = FileResponse(item.file.open("rb"), as_attachment=True, filename=Path(item.file.name).name)
    response["X-Content-Type-Options"] = "nosniff"
    response["Cache-Control"] = "private, no-store"
    return response
