from allianceauth import hooks
from allianceauth.services.hooks import MenuItemHook, UrlHook

from . import urls
from .service_hook import QQBotService

# Views reachable without an AA login. They authenticate with an HMAC
# signature instead. Must equal each URLPattern.lookup_str.
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
