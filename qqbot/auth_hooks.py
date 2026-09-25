import copy

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
    """Sidebar entry for members and QQ managers (DESIGN.md 4.1).

    Members go to their own page. A QQ manager without ``basic_access``
    (e.g. a non-Member state) cannot open that page, so for them the entry
    leads straight to the manage pages.
    """

    def __init__(self):
        super().__init__(
            "QQ 绑定",
            "fa-brands fa-qq",
            "qqbot:my_qq",
            navactive=["qqbot:"],
        )

    def render(self, request):
        user = request.user
        if user.has_perm("qqbot.basic_access"):
            return MenuItemHook.render(self, request)
        if user.has_perm("qqbot.manage"):
            # Render a copy: the hook object may be shared between requests.
            item = copy.copy(self)
            item.url_name = "qqbot:manage_index"
            return MenuItemHook.render(item, request)
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
