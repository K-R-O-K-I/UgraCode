import base64
import json
from decimal import Decimal
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen
from django.conf import settings
from django.core.exceptions import ValidationError
from .models import Checkout
from .services import complete, decline


def api(path, payload=None, key=None):
    if not settings.YOOKASSA_SHOP_ID or not settings.YOOKASSA_SECRET_KEY:
        raise ValidationError("Оплата пока не подключена. Обратитесь к мастеру.")
    credentials = f"{settings.YOOKASSA_SHOP_ID}:{settings.YOOKASSA_SECRET_KEY}"
    headers = {"Authorization": "Basic "+base64.b64encode(credentials.encode()).decode(), "Content-Type": "application/json"}
    if key:
        headers["Idempotence-Key"] = str(key)
    request = Request("https://api.yookassa.ru/v3/"+path, headers=headers,
                      data=json.dumps(payload).encode() if payload is not None else None)
    try:
        with urlopen(request, timeout=15) as response:
            return json.load(response)
    except (HTTPError, URLError, TimeoutError, ValueError) as error:
        raise ValidationError("Платёжный сервис временно недоступен. Повторите попытку.") from error


def start(checkout):
    from .documents import ensure_accepted
    ensure_accepted(checkout)
    if checkout.confirmation_url:
        return checkout.confirmation_url
    invoice = checkout.invoice
    result = api("payments", {
        "amount": {"value": str(invoice.amount), "currency": "RUB"},
        "capture": True,
        "confirmation": {"type": "redirect", "return_url": settings.PUBLIC_BASE_URL+f"/family/checkout/{checkout.pk}/"},
        "description": f"Абонемент, счёт {invoice.pk}",
        "metadata": {"invoice_id": str(invoice.pk), "checkout_id": str(checkout.pk)},
    }, checkout.pk)
    url = result.get("confirmation", {}).get("confirmation_url", "")
    parsed = urlparse(url)
    if parsed.scheme != "https" or not result.get("id"):
        raise ValidationError("Не удалось получить ссылку на оплату.")
    Checkout.objects.filter(pk=checkout.pk).update(provider_id=result["id"], confirmation_url=url)
    return url


def verify(checkout):
    if not checkout.provider_id:
        raise ValidationError("Платёж ещё не создан.")
    result = api("payments/"+checkout.provider_id)
    invoice = checkout.invoice
    try:
        valid = (
            result["id"] == checkout.provider_id
            and Decimal(result["amount"]["value"]) == invoice.amount
            and result["amount"]["currency"] == "RUB"
            and result["metadata"]["invoice_id"] == str(invoice.pk)
            and result["metadata"]["checkout_id"] == str(checkout.pk)
        )
    except (KeyError, TypeError, ArithmeticError):
        valid = False
    if not valid:
        raise ValidationError("Параметры платежа не совпадают со счётом.")
    if result.get("status") == "succeeded" and result.get("paid") is True:
        complete(checkout.pk)
    elif result.get("status") == "canceled":
        decline(checkout.pk)
