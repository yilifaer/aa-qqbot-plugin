from django.urls import path
from . import views

urlpatterns = [
    path("ping/", views.ping, name="qqbot_ping"),
    path("bind/", views.bind, name="qqbot_bind"),
    path("manage/", views.binding_list, name="qqbot_manage"),  # 仅管理员可见
]
