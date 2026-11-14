import calendar
import json
from datetime import date, timedelta
from pathlib import Path
from django.conf import settings
from django.contrib import messages
from django.core.exceptions import ValidationError
from django.db import IntegrityError
from django.db.models import Sum
from django.http import FileResponse, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.cache import never_cache
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST
from dashboard import services as master
from dashboard.models import Booking, Child, Family, Invoice, Lesson, Notification, Payment, Receipt, Subscription
from dashboard.views import staff_only, base_context as master_context
from . import forms, payments, services
from .models import AccessLink, Checkout
from .security import allow_attempt, family_only, token_hash

NAV = [("home", "Моя семья", "children"), ("calendar", "Занятия", "schedule"),
       ("children", "Дети", "children"), ("visits", "Посещения", "journal"), ("payments", "Оплаты", "payments")]


def context(request, page="home"):
    return {
        "family": request.family, "page": page, "family_nav": NAV,
        "unread": Notification.objects.filter(family=request.family, read_at__isnull=True).count(),
        "demo_payment": settings.PAYMENT_MODE == "demo",
    }


@never_cache
def enter(request, token):
    if not allow_attempt(request):
        return render(request, "families/access.html", status=429)
    link = AccessLink.objects.filter(token_hash=token_hash(token), revoked_at__isnull=True, expires_at__gt=timezone.now()).first()
    if link is None:
        return render(request, "families/access.html", status=403)
    request.session.cycle_key()
    request.session["family_link_id"] = link.pk
    return redirect("family-home")


@family_only
def home(request):
    page = request.GET.get("page", "home")
    if page not in [key for key, _, _ in NAV]+["notifications"]:
        page = "home"
    if request.method == "POST":
        if page != "notifications":
            return HttpResponse(status=405)
        Notification.objects.filter(family=request.family, read_at__isnull=True).update(read_at=timezone.now())
        return redirect("/family/?page=notifications")
    today = timezone.localdate()
    try:
        selected_day = date.fromisoformat(request.GET.get("date", ""))
    except ValueError:
        selected_day = today
    child_id = request.GET.get("child", "")
    children = request.family.children.filter(active=True).order_by("pk")
    selected_child = children.filter(pk=int(child_id)).first() if child_id.isdecimal() else None
    bookings = Booking.objects.filter(child__family=request.family).select_related("child", "lesson").order_by("lesson__starts_at")
    if selected_child:
        bookings = bookings.filter(child=selected_child)
    month_start = selected_day.replace(day=1)
    next_month = (month_start.replace(day=28)+timedelta(days=4)).replace(day=1)
    marked_dates = set(bookings.filter(status="booked", lesson__status="scheduled", lesson__starts_at__date__gte=month_start,
                                      lesson__starts_at__date__lt=next_month).values_list("lesson__starts_at", flat=True))
    marked_dates = {timezone.localdate(item) for item in marked_dates}
    calendar_days = [
        {"value": day, "in_month": day.month == selected_day.month, "marked": day in marked_dates}
        for day in calendar.Calendar(firstweekday=0).itermonthdates(selected_day.year, selected_day.month)
    ]
    child_cards = []
    for child in children:
        active_subscriptions = list(Subscription.objects.filter(
            invoice__child=child, starts_on__lte=today, expires_on__gte=today
        ).order_by("expires_on"))
        child_cards.append({
            "child": child, "balance": sum(item.balance for item in active_subscriptions),
            "expires": next((item.expires_on for item in active_subscriptions if item.balance > 0), None),
            "next": bookings.filter(child=child, status="booked", lesson__status="scheduled", lesson__starts_at__gte=timezone.now()).first(),
        })
    lessons = Lesson.objects.filter(starts_at__date=selected_day).order_by("starts_at")
    lesson_cards = [{"lesson": lesson, "family_bookings": list(bookings.filter(lesson=lesson)),
                     "can_book": lesson.starts_at > timezone.now() and lesson.status == "scheduled" and lesson.remaining > 0}
                    for lesson in lessons]
    invoices = Invoice.objects.filter(child__family=request.family, status="open")
    unpaid = sum(max(invoice.amount-(invoice.payment_set.aggregate(total=Sum("amount"))["total"] or 0), 0) for invoice in invoices)
    data = context(request, page)
    data.update({
        "today": today, "now": timezone.now(), "selected_day": selected_day, "calendar_days": calendar_days,
        "previous_month": month_start-timedelta(days=1), "next_month": next_month,
        "children": children, "child_cards": child_cards, "selected_child": selected_child,
        "lesson_cards": lesson_cards, "bookings": bookings.order_by("-lesson__starts_at"),
        "upcoming": bookings.filter(status="booked", lesson__status="scheduled", lesson__ends_at__gte=timezone.now())[:4],
        "payments": Payment.objects.filter(invoice__child__family=request.family).select_related("invoice__child").prefetch_related("receipts"),
        "checkouts": Checkout.objects.filter(invoice__child__family=request.family, status="pending").select_related("invoice__child"),
        "subscriptions": Subscription.objects.filter(invoice__child__family=request.family).select_related("invoice__child").order_by("-expires_on"),
        "unpaid": unpaid, "notifications": Notification.objects.filter(family=request.family)[:100],
    })
    return render(request, "families/home.html", data)


@family_only
def operation(request, action):
    titles = {"child": "Добавить ребёнка", "book": "Записаться на занятие", "cancel": "Отменить запись",
              "reason": "Сообщить причину отсутствия", "buy": "Купить абонемент"}
    if action not in titles:
        return HttpResponse(status=404)
    booking = None
    initial = {}
    if action in ("cancel", "reason"):
        if not request.GET.get("booking", "").isdecimal():
            return HttpResponse(status=404)
        booking = get_object_or_404(Booking.objects.select_related("lesson", "child"), pk=request.GET.get("booking"), child__family=request.family)
        initial["reason"] = booking.parent_reason
    if request.GET.get("child", "").isdecimal():
        initial["child"] = get_object_or_404(Child, pk=request.GET["child"], family=request.family)
    if request.GET.get("lesson", "").isdecimal():
        initial["lesson"] = get_object_or_404(Lesson, pk=request.GET["lesson"])
    form_class = {"child": forms.ChildForm, "book": forms.BookForm, "cancel": forms.ReasonForm,
                  "reason": forms.ReasonForm, "buy": forms.BuyForm}[action]
    kwargs = {"family": request.family} if action in ("book", "buy") else {}
    form = form_class(request.POST if request.method == "POST" else None, initial=initial, **kwargs)
    if request.method == "POST" and form.is_valid():
        data = form.cleaned_data
        try:
            if action == "child":
                services.add_child(request.family, data)
            elif action == "book":
                master.book(services.ParentActor(request.family), data)
            elif action == "cancel":
                services.parent_cancel(request.family, booking, data["reason"])
            elif action == "reason":
                services.absence_reason(request.family, booking, data["reason"])
            else:
                mode = settings.PAYMENT_MODE
                if mode not in ("demo", "yookassa") or (mode == "demo" and not settings.DEBUG):
                    raise ValidationError("Оплата пока не подключена. Обратитесь к мастеру.")
                checkout = services.create_checkout(request.family, data["child"], data["plan"], data["key"], mode)
                return redirect("family-checkout", pk=checkout.pk)
            messages.success(request, "Сохранено." if action == "reason" else "Готово.")
            return redirect("family-home")
        except ValidationError as error:
            form.add_error(None, error)
        except IntegrityError:
            form.add_error(None, "Запись уже изменилась. Обновите страницу и проверьте результат.")
    result = context(request)
    result.update(form=form, title=titles[action], action=action, booking=booking, lesson=initial.get("lesson"))
    return render(request, "families/form.html", result)


@family_only
def checkout_view(request, pk):
    checkout = get_object_or_404(Checkout.objects.select_related("invoice__child"), pk=pk, invoice__child__family=request.family)
    error = ""
    if request.method == "POST":
        action = request.POST.get("action")
        try:
            if checkout.provider == "demo":
                if not settings.DEBUG or settings.PAYMENT_MODE != "demo":
                    return HttpResponse(status=403)
                if action == "confirm":
                    services.complete(checkout.pk)
                elif action == "cancel":
                    services.decline(checkout.pk)
                else:
                    return HttpResponse(status=400)
            elif checkout.status == "pending":
                if action == "start":
                    return redirect(payments.start(checkout))
                if action == "verify":
                    payments.verify(checkout)
                else:
                    return HttpResponse(status=400)
            return redirect("family-checkout", pk=pk)
        except ValidationError as exception:
            error = " ".join(exception.messages)
    result = context(request, "payments")
    result.update(checkout=checkout, error=error)
    return render(request, "families/checkout.html", result)


@family_only
def receipt(request, pk):
    item = get_object_or_404(Receipt, pk=pk, payment__invoice__child__family=request.family)
    if item.file:
        return FileResponse(item.file.open("rb"), as_attachment=True, filename="chek"+Path(item.file.name).suffix)
    return redirect(item.external_url)


@family_only
@require_POST
def leave(request):
    request.session.pop("family_link_id", None)
    request.session.cycle_key()
    return render(request, "families/access.html", {"left": True})


@csrf_exempt
@require_POST
def webhook(request):
    if settings.PAYMENT_MODE != "yookassa":
        return HttpResponse(status=404)
    if len(request.body) > 65536:
        return HttpResponse(status=400)
    try:
        data = json.loads(request.body)
        provider_id = data["object"]["id"]
        if not isinstance(provider_id, str):
            return HttpResponse(status=400)
    except (ValueError, KeyError, TypeError):
        return HttpResponse(status=400)
    checkout = Checkout.objects.filter(provider_id=provider_id, provider="yookassa").first()
    if not checkout:
        return HttpResponse(status=404)
    try:
        payments.verify(checkout)
    except ValidationError:
        return HttpResponse(status=503)
    return HttpResponse(status=200)


@staff_only
@never_cache
def manage(request, pk=None):
    family = get_object_or_404(Family, pk=pk) if pk else None
    form = forms.FamilyForm(request.POST if request.method == "POST" else None, instance=family)
    if request.method == "POST" and form.is_valid():
        family = form.save()
        master.audit(request.user, f"Сохранены данные семьи · {family.contact_name}")
        return redirect("family-manage-detail", pk=family.pk)
    result = master_context(request, "children")
    result.update(families=Family.objects.prefetch_related("children"), family=family, form=form,
                  access_links=family.access_links.filter(revoked_at__isnull=True) if family else [])
    return render(request, "families/manage.html", result)


@staff_only
@never_cache
@require_POST
def access_action(request, pk):
    family = get_object_or_404(Family, pk=pk)
    if request.POST.get("action") == "revoke":
        services.revoke_links(family)
        master.audit(request.user, f"Отозван доступ семьи · {family.contact_name}")
        messages.success(request, "Ссылка отозвана. Открытые семейные кабинеты потеряют доступ.")
        return redirect("family-manage-detail", pk=pk)
    if request.POST.get("action") != "issue":
        return HttpResponse(status=400)
    token = services.issue_link(family)
    master.audit(request.user, f"Выдана ссылка семье · {family.contact_name}")
    result = master_context(request, "children")
    result.update(family=family, personal_url=request.build_absolute_uri(reverse("family-enter", args=[token])))
    return render(request, "families/link.html", result)
