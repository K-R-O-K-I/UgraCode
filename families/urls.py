from django.urls import path
from . import views
from accounts.views import child_avatar

urlpatterns = [
    path("avatar/<int:pk>/", child_avatar, name="family-avatar"),
    path("", views.home, name="family-home"),
    path("enter/<str:token>/", views.enter, name="family-enter"),
    path("form/<str:action>/", views.operation, name="family-operation"),
    path("checkout/<uuid:pk>/", views.checkout_view, name="family-checkout"),
    path("receipt/<int:pk>/", views.receipt, name="family-receipt"),
    path("leave/", views.leave, name="family-leave"),
    path("webhook/", views.webhook, name="family-webhook"),
]
