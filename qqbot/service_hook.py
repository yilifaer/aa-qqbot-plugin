"""The "QQ 绑定" card on AA's services page (docs/SPEC.md section 5).

The card is only an entry point to the member page. AA's service callbacks
(``validate_user``, ``delete_user``, ...) do nothing: correctness comes from
the signal handlers and the daily reconciliation, which judge every binding
from scratch anyway.
"""

from django.template.loader import render_to_string

from allianceauth.services.hooks import ServicesHook, get_extension_logger

logger = get_extension_logger(__name__)


class QQBotService(ServicesHook):
    def __init__(self):
        super().__init__()
        self.name = "qq"
        self.access_perm = "qqbot.basic_access"
        self.service_ctrl_template = "qqbot/service_ctrl.html"

    @property
    def title(self):
        return "QQ 绑定"

    def service_active_for_user(self, user):
        return user.has_perm(self.access_perm)

    # AA callbacks: intentionally no-ops ------------------------------------

    def validate_user(self, user):
        pass

    def delete_user(self, user, notify_user=False):
        return False

    def update_groups(self, user):
        pass

    def update_all_groups(self):
        pass

    def sync_nickname(self, user):
        """Main character renamed etc.: recompute the card (emits events)."""
        try:
            from .core.events import refresh_user

            refresh_user(user)
        except Exception:
            logger.exception("qqbot: sync_nickname failed for %s", user)

    # Card ---------------------------------------------------------------------

    def render_services_ctrl(self, request):
        from .core.access import has_main_character
        from .views.member import member_status

        user = request.user
        status = member_status(user)
        return render_to_string(
            self.service_ctrl_template,
            {
                "service_name": self.title,
                "username": status.masked_qq,
                "status": status,
                "has_main": has_main_character(user),
            },
            request=request,
        )
