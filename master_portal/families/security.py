import hashlib
import logging
import re
from functools import wraps
from django.core.cache import cache
from django.shortcuts import render
from django.utils import timezone
from django.views.decorators.cache import never_cache


class HideAccessLinks(logging.Filter):
    def filter(self, record):
        pattern = r"(/family/enter/)[^/\s?]+"
        if isinstance(record.msg, str):
            record.msg = re.sub(pattern, r"\1[hidden]", record.msg)
        if isinstance(record.args, tuple):
            record.args = tuple(re.sub(pattern, r"\1[hidden]", item) if isinstance(item, str) else item for item in record.args)
        return True


def token_hash(token):
    return hashlib.sha256(token.encode()).hexdigest()


def allow_attempt(request):
    key = "family-login:" + token_hash(request.META.get("REMOTE_ADDR", "unknown"))
    if cache.add(key, 1, 60):
        return True
    return cache.incr(key) <= 30


def family_only(view):
    @never_cache
    @wraps(view)
    def wrapped(request, *args, **kwargs):
        from .models import AccessLink
        link = AccessLink.objects.select_related("family").filter(
            pk=request.session.get("family_link_id"), revoked_at__isnull=True,
            expires_at__gt=timezone.now(),
        ).first()
        if link is None:
            request.session.pop("family_link_id", None)
            return render(request, "families/access.html", status=403)
        request.family = link.family
        request.family_link = link
        return view(request, *args, **kwargs)
    return wrapped
