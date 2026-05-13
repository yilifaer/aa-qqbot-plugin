from django.urls import path
from . import views

app_name = "qqbot"

urlpatterns = [
    path("", views.bind, name="qqbot_bind"),
    path("ping/", views.ping, name="qqbot_ping"),
    path("api/bind/", views.bind_api, name="qqbot_bind_api"),
    path("manage/", views.binding_list, name="qqbot_manage"),
]
