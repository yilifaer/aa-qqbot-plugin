"""URL layout.

The bot API lives in its own *nested* include. AA's ``decorate_url_patterns``
stops (``return``) at the first excluded view of a pattern list, which would
silently leave every later route in a flat list without login protection.
Keeping the public views in a nested include confines that to the API list.
Every non-public view is additionally protected by its own decorators.
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
