"""Member routes (included at ``/qqbot/`` in namespace ``qqbot``).

Members work in the "QQ 绑定" card on AA's services page; these are the
card's POST targets. ``my_qq`` (the old "我的 QQ" page) and GET on
``member_unbind`` only redirect back to the card, so old links keep working.
"""

from django.urls import path

from . import member

urlpatterns = [
    path("", member.my_qq, name="my_qq"),
    path("submit/", member.submit, name="member_submit"),
    path("code/cancel/", member.code_cancel, name="member_code_cancel"),
    path("nickname/", member.nickname, name="member_nickname"),
    path("unbind/", member.unbind, name="member_unbind"),
]
