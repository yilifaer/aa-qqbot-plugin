"""Alliance Auth hooks - register sidebar menu and URLs."""

from allianceauth import hooks
from allianceauth.services.hooks import MenuItemHook, UrlHook

from . import urls


class QqbotMenuItem(MenuItemHook):
    """Sidebar menu entry for QQ binding."""

    def __init__(self):
        super().__init__(
            text="QQ 绑定",
            classes="fa-solid fa-qq",
            url_name="qqbot:qqbot_bind",
            navactive=["qqbot:"],
        )

    def render(self, request):
        # Show the menu only to logged-in users
        if request.user.is_authenticated:
            return MenuItemHook.render(self, request)
        return ""


@hooks.register("menu_item_hook")
def register_menu():
    return QqbotMenuItem()


@hooks.register("url_hook")
def register_url():
    return UrlHook(urls, "qqbot", r"^qqbot/")
