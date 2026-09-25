from importlib import import_module

from django.apps import AppConfig


class QqbotConfig(AppConfig):
    name = "qqbot"
    label = "qqbot"
    verbose_name = "QQ 绑定"
    default_auto_field = "django.db.models.BigAutoField"

    def ready(self):
        # Imported for their side effects: registering the system checks
        # and connecting the signal receivers.
        import_module(f"{self.name}.checks")
        import_module(f"{self.name}.signals")
