"""Member page routes (included at ``/qqbot/`` in namespace ``qqbot``)."""

from django.urls import path

from . import member

urlpatterns = [
    path("", member.my_qq, name="my_qq"),
    path("submit/", member.submit, name="member_submit"),
    path("code/cancel/", member.code_cancel, name="member_code_cancel"),
    path("nickname/", member.nickname, name="member_nickname"),
    path("unbind/", member.unbind, name="member_unbind"),
]
