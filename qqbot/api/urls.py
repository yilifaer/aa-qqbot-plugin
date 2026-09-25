"""Bot API routes: ``/qqbot/api/v1/<name>/``, URL names ``api_<name>``.

Included by ``qqbot.urls`` as a nested include. AA's ``decorate_url_patterns``
stops at the first excluded (public) view of a pattern list, so this list
must hold only the public API views.
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
