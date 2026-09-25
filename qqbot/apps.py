from importlib import import_module

from django.apps import AppConfig
from django.utils.translation import gettext_lazy as _


class QqbotConfig(AppConfig):
    name = "qqbot"
    label = "qqbot"
    # Follows the admin user's language ("QQ binding" / "QQ 绑定"); short, so
    # the permission picker still shows "member" / "manager".
    # 跟随后台用户的语言（"QQ binding" / 「QQ 绑定」）；名字短，权限选择框里
    # 还能看到 "member" / "manager"。
    verbose_name = _("QQ binding")
    default_auto_field = "django.db.models.BigAutoField"

    def ready(self):
        # Imported for their side effects: registering the system checks
        # and connecting the signal receivers.
        # 导入这两个模块是为了它们的副作用：注册系统检查、连接信号接收器。
        import_module(f"{self.name}.checks")
        import_module(f"{self.name}.signals")
