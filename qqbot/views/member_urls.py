"""Member routes (included at ``/qqbot/`` in namespace ``qqbot``).

Members work in the "QQ 绑定" card on AA's services page; these are the
card's POST targets. ``my_qq`` (the old "我的 QQ" page) and GET on
``member_unbind`` only redirect back to the card, so old links keep working.

成员路由（挂在 ``/qqbot/`` 下，命名空间为 ``qqbot``）。

成员在 AA 服务页的「QQ 绑定」卡片里操作，这些路由就是卡片表单的 POST 目标。
``my_qq``（原来的「我的 QQ」页面）以及对 ``member_unbind`` 的 GET 请求只会
跳回卡片，这样旧链接仍然能用。
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
