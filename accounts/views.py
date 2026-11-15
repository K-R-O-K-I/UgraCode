import re

from django.shortcuts import redirect, render
from django.utils import timezone
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_POST, require_safe

from .models import AccessLink
from .services import hash_token

SESSION_KEY = "family_access_link_id"


def active_links():
    return AccessLink.objects.filter(revoked_at__isnull=True, expires_at__gt=timezone.now(), family__active=True)


def family_from_request(request):
    link_id = request.session.get(SESSION_KEY)
    if link_id is None:
        return None
    link = active_links().select_related("family").filter(pk=link_id).first()
    if link is None:
        request.session.pop(SESSION_KEY, None)
        return None
    return link.family


@never_cache
@require_safe
def family_login(request, token):
    # Token URL is exchanged for a session. Keep it out of referrer headers.
    link = None
    if re.fullmatch(r"[A-Za-z0-9_-]{43}", token):
        link = active_links().filter(token_hash=hash_token(token)).first()
    if link is None:
        response = render(request, "accounts/unavailable.html", status=404)
    else:
        request.session.cycle_key()
        request.session[SESSION_KEY] = link.pk
        response = redirect("family-home")
    response["Referrer-Policy"] = "no-referrer"
    return response


@never_cache
@require_POST
def family_logout(request):
    request.session.pop(SESSION_KEY, None)
    request.session.cycle_key()
    return redirect("home")


@never_cache
@require_safe
def child_avatar(request, pk):
    from django.http import FileResponse, Http404
    from django.shortcuts import get_object_or_404
    from .models import Child
    child = get_object_or_404(Child, pk=pk)
    if not (request.user.is_authenticated and request.user.is_staff):
        family = family_from_request(request)
        if family is None or family.pk != child.family_id:
            raise Http404
    if not child.avatar:
        raise Http404
    response = FileResponse(child.avatar.open("rb"))
    response["X-Content-Type-Options"] = "nosniff"
    return response
