from django.contrib.auth import views as auth_views
from django.urls import path
from dashboard import views
urlpatterns = [
    path("login/", auth_views.LoginView.as_view(template_name="dashboard/login.html"), name="login"),
    path("logout/", auth_views.LogoutView.as_view(), name="logout"),
    path("", views.home, name="home"),
    path("form/<str:action>/", views.operation, name="operation"),
    path("export/<str:kind>/", views.export_csv, name="export"),
    path("receipt/<int:pk>/", views.receipt, name="receipt"),
]
