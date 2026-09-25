from allianceauth import hooks
from allianceauth.services.hooks import MenuItemHook, UrlHook

from . import urls
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
    """Sidebar entry "QQ 管理", only for QQ managers (DECISIONS.md #18).

    Members have no entry of their own: everything they do is in the
    "QQ 绑定" card on AA's services page (AA's own "服务" menu entry).

    侧边栏的“QQ 管理”入口，只有 QQ 管理员能看到（DECISIONS.md #18）。

    普通成员没有单独的入口：他们要做的事都在 AA 服务页面上的“QQ 绑定”
    卡片里（也就是 AA 自带的“服务”菜单）。
    """

    def __init__(self):
        super().__init__(
            "QQ 管理",
            "fa-brands fa-qq",
            "qqbot:manage_index",
            navactive=["qqbot:"],
        )

    def render(self, request):
        if request.user.has_perm("qqbot.manage"):
            return MenuItemHook.render(self, request)
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
