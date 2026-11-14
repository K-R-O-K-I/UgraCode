from django.contrib import admin
from django.urls import path, include, re_path
from django.views.generic import RedirectView

from accounts import views as account_views

admin.site.site_header = "Мастерская Ясна"
admin.site.site_title = "Ясна"
admin.site.index_title = "Управление"

urlpatterns = [
    path("children/<int:pk>/avatar/", account_views.child_avatar, name="child-avatar"),
    path('family/access/<str:token>/', account_views.family_login, name='family-login'),
    path('family/', include('families.urls')),
    path('family/logout/', account_views.family_logout, name='family-logout'),
    path("", include("dashboard.urls")),
    re_path(r"^admin(?:/.*)?$", RedirectView.as_view(url="/families/", permanent=False)),
]
