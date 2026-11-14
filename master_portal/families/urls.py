from django.urls import path
from . import views

urlpatterns = [
    path("", views.home, name="family-home"),
    path("enter/<str:token>/", views.enter, name="family-enter"),
    path("form/<str:action>/", views.operation, name="family-operation"),
    path("checkout/<uuid:pk>/", views.checkout_view, name="family-checkout"),
    path("avatar/<int:pk>/", views.avatar, name="family-avatar"),
    path("receipt/<int:pk>/", views.receipt, name="family-receipt"),
    path("leave/", views.leave, name="family-leave"),
    path("webhook/", views.webhook, name="family-webhook"),
    path("manage/", views.manage, name="family-manage"),
    path("manage/<int:pk>/", views.manage, name="family-manage-detail"),
    path("manage/<int:pk>/access/", views.access_action, name="family-access-action"),
]
