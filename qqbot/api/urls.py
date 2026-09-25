"""Bot API routes: ``/qqbot/api/v1/<name>/``, URL names ``api_<name>``.

Included by ``qqbot.urls`` as a nested include. AA's ``decorate_url_patterns``
stops at the first excluded (public) view of a pattern list, so this list
must hold only the public API views.

机器人接口的路由：``/qqbot/api/v1/<name>/``，URL 名称为 ``api_<name>``。

由 ``qqbot.urls`` 以嵌套 include 的方式引入。AA 的 ``decorate_url_patterns``
在一个路由列表里遇到第一个被排除的（公开）视图就会停止处理，所以这份列表里
只能放公开的接口视图。
"""

from django.urls import path

from . import views

urlpatterns = [
    path("health/", views.health, name="api_health"),
    path("groups/", views.groups, name="api_groups"),
    path("check/", views.check, name="api_check"),
    path("claim/", views.claim, name="api_claim"),
    path("events/", views.events, name="api_events"),
]
