"""URL layout.

The bot API lives in its own *nested* include. AA's ``decorate_url_patterns``
stops (``return``) at the first excluded view of a pattern list, which would
silently leave every later route in a flat list without login protection.
Keeping the public views in a nested include confines that to the API list.
Every non-public view is additionally protected by its own decorators.

URL 结构。

机器人接口放在单独的“嵌套” include 里。AA 的 ``decorate_url_patterns``
在一个路由列表里遇到第一个被排除的视图就会停下（``return``），这会让平铺
列表里排在它后面的所有路由悄悄失去登录保护。把公开视图放进嵌套 include，
就把这个影响限制在接口列表里。每个非公开视图另外还有自己的装饰器保护。
"""

from django.urls import include, path

from .api import urls as api_urls
from .views import manage_urls, member_urls

app_name = "qqbot"

urlpatterns = [
    path("", include(member_urls.urlpatterns)),
    path("manage/", include(manage_urls.urlpatterns)),
    path("api/v1/", include(api_urls.urlpatterns)),
]
