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
from django.views.decorators.http import require_POST, require_safe
from dashboard import services as master
from dashboard.models import Booking, Child, Family, Invoice, Lesson, Notification, Payment, Receipt, Subscription
from dashboard.views import staff_only, base_context as master_context
from . import forms, payments, services
from .models import AccessLink, Checkout
from .security import allow_attempt, family_only, token_hash

NAV = [("calendar", "Запись", "schedule"), ("upcoming", "Ближайшее", "journal"),
       ("children", "Дети", "children")]


def context(request, page="calendar"):
    return {
        "family": request.family, "page": page, "family_nav": NAV,
        "unread": Notification.objects.filter(family=request.family, read_at__isnull=True).count(),
        "demo_payment": settings.PAYMENT_MODE == "demo",
    }


@never_cache
@require_safe
def enter(request, token):
    if not allow_attempt(request):
        return render(request, "families/access.html", status=429)
    link = AccessLink.objects.filter(token_hash=token_hash(token), revoked_at__isnull=True, expires_at__gt=timezone.now(), family__active=True).first()
    if link is None:
        return render(request, "families/access.html", status=403)
    request.session.cycle_key()
    request.session["family_access_link_id"] = link.pk
    response = redirect("family-home")
    response["Referrer-Policy"] = "no-referrer"
    return response


@family_only
def home(request):
    page = request.GET.get("page", "calendar")
    if page not in {"calendar", "upcoming", "children", "visits", "payments", "notifications"}:
        page = "calendar"
    notes = Notification.objects.filter(family=request.family)
    if request.method == "POST":
        if page != "notifications":
            return HttpResponse(status=405)
        notes.filter(read_at__isnull=True).update(read_at=timezone.now())
        return redirect("/family/?page=notifications")

    now = timezone.now()
    today = timezone.localdate(now)
    children = request.family.children.all().defer("avatar").order_by("pk")
    bookings = Booking.objects.filter(child__family=request.family).select_related(
        "child", "lesson",
    ).defer("child__avatar").order_by("lesson__starts_at", "pk")
    data = context(request, page)
    data.update(today=today, now=now, children=children)

    if page in {"calendar", "visits"}:
        child_id = request.GET.get("child", "")
        selected_child = children.filter(pk=int(child_id)).first() if child_id.isascii() and child_id.isdecimal() and len(child_id) <= 18 else None
        if selected_child:
            bookings = bookings.filter(child=selected_child)
        data["selected_child"] = selected_child

    if page == "calendar":
        try:
            selected_day = date.fromisoformat(request.GET.get("date", ""))
            if not 2 <= selected_day.year <= 9998:
                selected_day = today
        except ValueError:
            selected_day = today
        month_start = selected_day.replace(day=1)
        next_month = (month_start.replace(day=28) + timedelta(days=4)).replace(day=1)
        month_lessons = Lesson.objects.filter(
            starts_at__date__gte=month_start, starts_at__date__lt=next_month,
        )
        lesson_dates = {
            timezone.localdate(start)
            for start in month_lessons.filter(status="scheduled").values_list("starts_at", flat=True)
        }
        booked_dates = {
            timezone.localdate(start)
            for start in bookings.filter(
                lesson__in=month_lessons, lesson__status="scheduled", status="booked",
            ).values_list("lesson__starts_at", flat=True)
        }
        lessons = month_lessons.filter(starts_at__date=selected_day)
        data.update(
            selected_day=selected_day, previous_month=month_start - timedelta(days=1),
            next_month=next_month,
            calendar_days=[
                {"value": day, "in_month": day.month == selected_day.month,
                 "has_lessons": day in lesson_dates, "booked": day in booked_dates}
                for day in calendar.Calendar().itermonthdates(selected_day.year, selected_day.month)
            ],
            lesson_cards=[
                {"lesson": lesson, "family_bookings": list(bookings.filter(lesson=lesson)),
                 "can_book": lesson.starts_at > now and lesson.status == "scheduled" and lesson.remaining > 0}
                for lesson in lessons
            ],
        )
    elif page == "upcoming":
        next_bookings = {}
        for booking in bookings.filter(
            status="booked", lesson__status="scheduled", lesson__starts_at__gte=now,
        ):
            next_bookings.setdefault(booking.child_id, booking)
        data["next_lessons"] = [(child, next_bookings.get(child.pk)) for child in children]
    elif page == "children":
        data["child_cards"] = []
        for child in children:
            subscriptions = Subscription.objects.filter(
                invoice__child=child, starts_on__lte=today, expires_on__gte=today,
            ).order_by("expires_on")
            balances = [(item.balance, item.expires_on) for item in subscriptions]
            data["child_cards"].append({
                "child": child, "has_avatar": bool(child.avatar),
                "balance": sum(balance for balance, _ in balances),
                "expires": next((expiry for balance, expiry in balances if balance > 0), None),
            })
    elif page == "visits":
        data["bookings"] = bookings.order_by("-lesson__starts_at")
    elif page == "payments":
        invoices = Invoice.objects.filter(child__family=request.family, status="open")
        data.update(
            unpaid=sum(max(invoice.amount - (invoice.payment_set.aggregate(total=Sum("amount"))["total"] or 0), 0) for invoice in invoices),
            payments=Payment.objects.filter(invoice__child__family=request.family).select_related("invoice__child").prefetch_related("receipts"),
            checkouts=Checkout.objects.filter(invoice__child__family=request.family, status="pending").select_related("invoice__child", "lesson"),
        )
    elif page == "notifications":
        data["notifications"] = notes[:100]
    return render(request, "families/home.html", data)


@family_only
def operation(request, action):
    titles = {"edit": "Редактировать профиль", "child": "Добавить ребёнка", "book": "Записаться на занятие", "cancel": "Отменить запись",
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
    if action == "child" and "child" in initial:
        action = "edit"
    if action == "edit" and "child" not in initial:
        return HttpResponse(status=404)
    if request.GET.get("lesson", "").isdecimal():
        initial["lesson"] = get_object_or_404(Lesson, pk=request.GET["lesson"])
    form_class = {"child": forms.ChildForm, "edit": forms.ChildForm, "book": forms.BookForm, "cancel": forms.ReasonForm,
                  "reason": forms.ReasonForm, "buy": forms.BuyForm}[action]
    kwargs = {"family": request.family} if action in ("book", "buy") else {}
    if action == "edit":
        kwargs["instance"] = initial["child"]
    if action == "book":
        kwargs["lesson"] = initial.get("lesson")
    form = form_class(
        request.POST if request.method == "POST" else None,
        files=request.FILES if request.method == "POST" else None,
        initial=initial, **kwargs,
    )
    if request.method == "POST" and form.is_valid():
        data = form.cleaned_data
        try:
            if action == "child":
                services.add_child(request.family, data)
            elif action == "edit":
                form.save()
            elif action == "book":
                if settings.PAYMENT_MODE != "demo" or not settings.DEBUG:
                    raise ValidationError("Тестовая оплата записи пока недоступна.")
                checkout = services.create_booking_checkout(request.family, data["child"], data["lesson"])
                return redirect("family-checkout", pk=checkout.pk)
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
            return redirect("/family/?page=" + ("children" if action in {"child", "edit"} else "upcoming"))
        except ValidationError as error:
            form.add_error(None, error)
        except IntegrityError:
            form.add_error(None, "Запись уже изменилась. Обновите страницу и проверьте результат.")
    page = "children" if action in {"child", "edit", "buy"} else "upcoming" if booking else "calendar"
    result = context(request, page)
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
    result = context(request, "calendar" if checkout.lesson_id else "children")
    result.update(checkout=checkout, error=error)
    return render(request, "families/checkout.html", result)


@family_only
def receipt(request, pk):
    item = get_object_or_404(Receipt, pk=pk, payment__invoice__child__family=request.family)
    if item.file:
        return FileResponse(item.file.open("rb"), as_attachment=True, filename="chek"+Path(item.file.name).suffix)
    if not item.external_url.startswith("https://"):
        return HttpResponse(status=404)
    return redirect(item.external_url)


@family_only
@require_POST
def leave(request):
    request.session.pop("family_access_link_id", None)
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
