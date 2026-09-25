from django.apps import AppConfig


class QqbotConfig(AppConfig):
    name = "qqbot"
    label = "qqbot"
    verbose_name = "QQ 绑定"
    default_auto_field = "django.db.models.BigAutoField"

    def ready(self):
        from . import checks, signals  # noqa: F401
