from pathlib import Path
import csv
from datetime import date, timedelta
from functools import wraps
from urllib.parse import urlencode
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError
from django.db.models import Q, Sum
from django.http import FileResponse, HttpResponse, HttpResponseRedirect
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from . import forms, services
from .models import AuditEvent, Booking, Child, Lesson, LessonPolicy, Notification, Payment, Receipt

PAGES = [
    ("schedule", "Расписание", "▦"), ("journal", "Журнал", "▤"),
    ("children", "Ученики", "♧"), ("payments", "Оплаты", "₽"),
    ("history", "История", "↶"), ("settings", "Правила", "⚙"),
]


def staff_only(view):
    @login_required
    @wraps(view)
    def wrapped(request, *args, **kwargs):
        if not request.user.is_staff:
            raise PermissionDenied("Доступ разрешён мастеру.")
        return view(request, *args, **kwargs)
    return wrapped


def base_context(request, page="schedule"):
    return {
        "page": page, "nav": PAGES,
        "page_title": dict((key, name) for key, name, _ in PAGES).get(page, "Мастерская"),
        "unread": Notification.objects.filter(family__isnull=True, read_at__isnull=True).count(),
    }


@staff_only
def home(request):
    page = request.GET.get("page", "schedule")
    if page not in dict((key, name) for key, name, _ in PAGES) and page != "notifications":
        page = "schedule"
    context = base_context(request, page)
    try:
        day = date.fromisoformat(request.GET.get("date", ""))
    except ValueError:
        day = timezone.localdate()
    query = request.GET.get("q", "").strip()
    kind = request.GET.get("kind", "")
    daily = Lesson.objects.filter(starts_at__date=day)
    lessons = daily
    if kind in ("group", "individual"):
        lessons = lessons.filter(kind=kind)
    if query:
        lessons = lessons.filter(title__icontains=query)
    lesson_id = request.GET.get("lesson", "")
    selected = daily.filter(pk=int(lesson_id)).first() if lesson_id.isdecimal() else None
    selected = selected or daily.filter(status="scheduled").first() or daily.first()
    monday = day - timedelta(days=day.weekday())
    week = []
    for index in range(7):
        value = monday + timedelta(days=index)
        week.append({"date": value, "has_lessons": Lesson.objects.filter(starts_at__date=value, status="scheduled").exists()})
    active = daily.filter(status="scheduled")
    bookings = Booking.objects.filter(lesson__in=active, status="booked")
    counts = {
        "lessons": active.count(), "bookings": bookings.count(),
        "places": (active.aggregate(total=Sum("capacity"))["total"] or 0) - bookings.count(),
    }
    children = Child.objects.select_related("family").filter(family__active=True)
    if page == "children" and query:
        children = children.filter(Q(name__icontains=query) | Q(family__contact_name__icontains=query))
    context.update({
        "day": day, "today": timezone.localdate(), "week": week,
        "previous": day - timedelta(days=7), "following": day + timedelta(days=7),
        "lessons": lessons, "selected": selected, "query": query, "kind": kind,
        "counts": counts, "children": children, "low_balance": [child for child in children if child.balance <= 1][:3],
        "payments": Payment.objects.select_related("invoice__child").prefetch_related("receipts"),
        "audit": AuditEvent.objects.all()[:200], "notifications": Notification.objects.filter(family__isnull=True)[:100],
    })
    if request.method == "POST":
        if page != "notifications":
            return HttpResponse(status=405)
        Notification.objects.filter(family__isnull=True, read_at__isnull=True).update(read_at=timezone.now())
        messages.success(request, "Уведомления отмечены прочитанными.")
        return redirect("/?page=notifications")
    return render(request, "dashboard/home.html", context)


ACTIONS = {
    "lesson": (forms.LessonForm, "Новое занятие", services.save_lesson),
    "booking": (forms.BookingForm, "Записать ребёнка", services.book),
    "attendance": (forms.AttendanceForm, "Отметка посещения", services.mark),
    "cancel": (forms.CancelForm, "Отменить занятие", services.cancel),
    "move": (forms.MoveForm, "Перенести запись", services.move),
    "payment": (forms.PaymentForm, "Внести оплату", services.pay),
    "receipt": (forms.ReceiptForm, "Прикрепить чек", services.attach_receipt),
    "policy": (forms.PolicyForm, "Правила новых занятий", services.policy),
}


@staff_only
def operation(request, action):
    if action not in ACTIONS:
        return HttpResponse(status=404)
    form_class, title, handler = ACTIONS[action]
    initial = {}
    for field in ("lesson", "child", "booking", "payment"):
        value = request.GET.get(field)
        if value and value.isdecimal():
            initial[field] = int(value)
    booking = None
    if "booking" in initial:
        booking = get_object_or_404(Booking.objects.select_related("lesson", "child"), pk=initial["booking"])
    lesson = None
    pk = request.GET.get("id", "")
    if action == "lesson":
        if pk.isdecimal():
            lesson = get_object_or_404(Lesson, pk=int(pk))
            title = "Изменить занятие"
            initial = {field: getattr(lesson, field) for field in forms.LessonForm.base_fields}
        else:
            start = timezone.localtime().replace(hour=16, minute=0, second=0, microsecond=0)
            initial.update(starts_at=start, ends_at=start + timedelta(minutes=90))
    if action == "attendance":
        initial["attendance"] = request.GET.get("value", "present")
    if action == "policy":
        current = LessonPolicy.objects.order_by("-pk").first()
        if current:
            initial = {field: getattr(current, field) for field in forms.PolicyForm.base_fields}
    form = form_class(request.POST or None, request.FILES or None, initial=initial)
    if request.method == "POST" and form.is_valid():
        try:
            if action == "lesson":
                result = handler(request.user, form.cleaned_data, pk=lesson.pk if lesson else None)
                destination = "/?" + urlencode({"date": timezone.localdate(result.starts_at).isoformat(), "lesson": result.pk})
            else:
                handler(request.user, form.cleaned_data)
                destination = "/"
                if action in ("payment", "receipt"):
                    destination = "/?page=payments"
                elif action == "child":
                    destination = "/?page=children"
                elif action == "policy":
                    destination = "/?page=settings"
                elif action in ("attendance", "cancel", "move", "booking"):
                    event = form.cleaned_data.get("lesson") or form.cleaned_data["booking"].lesson
                    destination = "/?" + urlencode({"date": timezone.localdate(event.starts_at).isoformat(), "lesson": event.pk})
            messages.success(request, "Сохранено.")
            return redirect(destination)
        except ValidationError as error:
            form.add_error(None, error)
        except IntegrityError:
            form.add_error(None, "Данные уже изменились. Проверьте записи и повторите действие.")
    context = base_context(request)
    context.update(form=form, title=title, booking=booking, operation=action)
    return render(request, "dashboard/form.html", context)


@staff_only
def export_csv(request, kind):
    if kind == "children":
        rows = [["Ребёнок", "Родитель", "Остаток занятий"]]
        rows += [[child.name, child.family.contact_name, child.balance] for child in Child.objects.select_related("family")]
    elif kind == "payments":
        rows = [["Дата", "Ученик", "Сумма", "Способ"]]
        rows += [[payment.confirmed_at.isoformat(), payment.invoice.child.name, payment.amount, payment.get_method_display()] for payment in Payment.objects.select_related("invoice__child")]
    elif kind == "attendance":
        rows = [["Занятие", "Ученик", "Статус", "Отметка"]]
        rows += [[booking.lesson.title, booking.child.name, booking.status, booking.get_attendance_display()] for booking in Booking.objects.select_related("lesson", "child")]
    else:
        return HttpResponse(status=404)
    response = HttpResponse(content_type="text/csv; charset=utf-8")
    generated_at = timezone.localtime()
    response["Content-Disposition"] = f'attachment; filename="yasna-{kind}-{generated_at:%Y-%m-%d-%H%M%S}.csv"'
    response.write("\ufeff")
    writer = csv.writer(response, delimiter=";")
    writer.writerow(["Дата формирования", generated_at.strftime("%d.%m.%Y %H:%M:%S"), "Asia/Yekaterinburg"])
    for row in rows:
        writer.writerow(["'" + str(value) if str(value).startswith(("=", "+", "-", "@")) else value for value in row])
    return response


@staff_only
def receipt(request, pk):
    item = get_object_or_404(Receipt, pk=pk)
    if item.file:
        return FileResponse(item.file.open("rb"), as_attachment=True, filename="chek" + Path(item.file.name).suffix)
    if item.external_url.startswith("https://"):
        return HttpResponseRedirect(item.external_url)
    return HttpResponse(status=404)
