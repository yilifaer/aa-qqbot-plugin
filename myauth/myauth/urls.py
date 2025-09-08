from django.contrib import admin
from django.urls import path, include
from django.conf.urls.i18n import i18n_patterns

urlpatterns = [
    path("qqbot/", include("qqbot.urls")),
]

urlpatterns += i18n_patterns(
    path("admin/", admin.site.urls),
)

handler500 = "allianceauth.views.Generic500Redirect"
handler404 = "allianceauth.views.Generic404Redirect"
handler403 = "allianceauth.views.Generic403Redirect"
handler400 = "allianceauth.views.Generic400Redirect"
