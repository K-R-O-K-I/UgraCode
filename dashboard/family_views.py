from django import forms
from django.contrib import messages
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Q
from accounts.forms import FamilyForm
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_POST

from accounts.models import Family, Child
from accounts.services import update_family_link
from .models import Booking, Invoice, Notification
from .services import mark
from .views import staff_only, base_context


@staff_only
@never_cache
def families(request):
    context = base_context(request, "families")
    query = request.GET.get("q", "").strip()
    items = Family.objects.filter(active=True).prefetch_related("children")
    if query:
        items = items.filter(Q(contact_name__icontains=query) | Q(children__name__icontains=query)).distinct()
    context.update(page_title="Семьи", families=items, query=query)
    return render(request, "dashboard/families.html", context)


@staff_only
@never_cache
def edit_family(request):
    form = FamilyForm(request.POST if request.method == "POST" else None)
    if request.method == "POST" and form.is_valid():
        form.save()
        return redirect("families")
    context = base_context(request, "families")
    context.update(page_title="Новая семья", form=form)
    return render(request, "dashboard/family_form.html", context)


@staff_only
@require_POST
@never_cache
def family_action(request, pk, action):
    if action not in ("issue", "revoke", "delete"):
        from django.http import Http404
        raise Http404
    family = get_object_or_404(Family, pk=pk, active=True)
    if action == "issue":
        token = update_family_link(pk, issue=True)
        context = base_context(request, "families")
        context.update(page_title="Ссылка для семьи", family=family,
                       link=request.build_absolute_uri(reverse("family-login", args=[token])))
        response = render(request, "dashboard/family_link.html", context)
        response["Referrer-Policy"] = "same-origin"
        return response
    if action == "revoke":
        update_family_link(pk, issue=False)
        messages.success(request, "Ссылка отозвана.")
    elif action == "delete":
        with transaction.atomic():
            family = Family.objects.select_for_update().get(pk=pk)
            children = list(family.children.select_for_update())
            family.access_links.update(revoked_at=timezone.now())
            has_history = Booking.objects.filter(child__in=children).exists() or Invoice.objects.filter(child__in=children).exists() or Notification.objects.filter(family=family).exists()
            if has_history:
                family.active = False
                family.save(update_fields=["active"])
                messages.success(request, "Семья перенесена в архив, доступ закрыт. История занятий и оплат сохранена.")
            else:
                family.access_links.all().delete()
                family.children.all().delete()
                family.delete()
                messages.success(request, "Семья удалена.")
    else:
        return render(request, "accounts/unavailable.html", status=404)
    return redirect("families")


@staff_only
@require_POST
def quick_attendance(request, pk):
    booking = get_object_or_404(Booking.objects.select_related("lesson"), pk=pk)
    value = request.POST.get("attendance")
    if value not in ("present", "absent", "pending"):
        messages.error(request, "Неизвестная отметка.")
    else:
        try:
            mark(request.user, {"booking": booking, "attendance": value,
                                "reason": "Изменение мастером в журнале"})
        except ValidationError as error:
            messages.error(request, " ".join(error.messages))
        else:
            messages.success(request, "Отметка сохранена.")
    day = timezone.localdate(booking.lesson.starts_at).isoformat()
    page = "journal" if request.POST.get("return_page") == "journal" else "schedule"
    period = request.POST.get("return_period", "day")
    if period not in {"day", "week", "month"}:
        period = "day"
    return redirect(f"/?page={page}&date={day}&period={period}&lesson={booking.lesson_id}")


@staff_only
def child_profile(request, pk):
    child = get_object_or_404(Child.objects.select_related("family"), pk=pk)
    context = base_context(request, "families")
    context.update(child=child, page_title=child.name)
    return render(request, "dashboard/child_profile.html", context)
