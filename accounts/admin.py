from django.contrib import admin, messages
from django.contrib.auth.models import Group, User
from django.urls import reverse
from django.utils.html import format_html

from .models import Child, Family
from .services import update_family_link

admin.site.unregister(Group)
admin.site.unregister(User)


class ChildInline(admin.TabularInline):
    model = Child
    extra = 0


@admin.register(Family)
class FamilyAdmin(admin.ModelAdmin):
    list_display = ("contact_name", "phone")
    search_fields = ("contact_name", "phone")
    readonly_fields = ("contact_name", "phone")
    actions = ("issue_link", "revoke_links")

    def get_actions(self, request):
        actions = super().get_actions(request)
        actions.pop("delete_selected", None)
        return actions

    @admin.action(permissions=["change"], description="Выдать новую ссылку семье")
    def issue_link(self, request, queryset):
        families = list(queryset[:2])
        if len(families) != 1:
            self.message_user(request, "Выберите одну семью.", level=messages.ERROR)
            return
        token = update_family_link(families[0].pk, issue=True)
        url = request.build_absolute_uri(reverse("family-login", args=[token]))
        # Keep the token out of persistent session and message storage.
        from django.shortcuts import render

        response = render(request, "accounts/issued_link.html", {"url": url})
        response["Cache-Control"] = "no-store"
        response["Referrer-Policy"] = "same-origin"
        return response

    @admin.action(permissions=["change"], description="Отозвать ссылки выбранных семей")
    def revoke_links(self, request, queryset):
        for family_id in queryset.values_list("pk", flat=True):
            update_family_link(family_id, issue=False)
        self.message_user(request, "Ссылки отозваны.", level=messages.SUCCESS)
