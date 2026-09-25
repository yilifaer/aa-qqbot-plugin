"""TODO: ServicesHook card."""

from allianceauth.services.hooks import ServicesHook


class QQBotService(ServicesHook):
    def __init__(self):
        super().__init__()
        self.name = "qq"
        self.access_perm = "qqbot.basic_access"

    def service_active_for_user(self, user):
        return user.has_perm(self.access_perm)
