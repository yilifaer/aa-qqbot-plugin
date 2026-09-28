from django.utils.translation import gettext_lazy

from allianceauth import hooks
from allianceauth.services.hooks import MenuItemHook, UrlHook

from . import i18n, urls
from .core import attention
from .core.access import has_main_character
from .service_hook import QQBotService

# Views reachable without an AA login. They authenticate with an HMAC
# signature instead. Must equal each URLPattern.lookup_str.
# 不用登录 AA 就能访问的视图，它们改用 HMAC 签名认证。
# 每一项必须与对应的 URLPattern.lookup_str 完全相同。
PUBLIC_VIEWS = [
    "qqbot.api.views.health",
    "qqbot.api.views.groups",
    "qqbot.api.views.check",
    "qqbot.api.views.claim",
    "qqbot.api.views.events",
]


class QQBotMenuItem(MenuItemHook):
    """Sidebar entry "QQ 管理", only for QQ managers with a main character
    (DECISIONS.md #18), with a number badge for what needs them (#24).

    Members have no entry of their own: everything they do is in the
    "QQ 绑定" card on AA's services page (AA's own "服务" menu entry).

    侧边栏的“QQ 管理”入口，只有有主角色的 QQ 管理员能看到（DECISIONS.md #18），
    需要他们处理的事用数字角标显示（#24）。

    普通成员没有单独的入口：他们要做的事都在 AA 服务页面上的“QQ 绑定”
    卡片里（也就是 AA 自带的“服务”菜单）。
    """

    def __init__(self):
        super().__init__(
            gettext_lazy("QQ Admin"),
            "fa-brands fa-qq",
            "qqbot:manage_index",
            navactive=["qqbot:"],
        )

    def render(self, request):
        # AA sends users without a main character from every plugin page back
        # to the dashboard, so the entry would lead nowhere for them.
        # AA 会把没有主角色的用户从所有插件页面跳回首页，所以对他们不显示这个入口。
        if request.user.has_perm("qqbot.manage") and has_main_character(request.user):
            # The number badge, like AA's own group requests entry. The hook
            # object may be reused between requests: set it every time.
            # 数字角标，和 AA 自带的「入组申请」一样。hook 对象可能被多个请求重复使用，
            # 每次都要重新赋值。
            self.count = attention.attention_counts()["total"] or None
            # qqbot's UI language (``qqbot.i18n``) / qqbot 的界面语言
            with i18n.override():
                return MenuItemHook.render(self, request)
        self.count = None
        return ""


@hooks.register("menu_item_hook")
def register_menu():
    return QQBotMenuItem()


@hooks.register("url_hook")
def register_urls():
    return UrlHook(urls, "qqbot", r"^qqbot/", excluded_views=PUBLIC_VIEWS)


@hooks.register("services_hook")
def register_service():
    return QQBotService()
