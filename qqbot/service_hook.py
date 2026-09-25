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

    # update_groups / update_all_groups are deliberately NOT overridden: AA's
    # User admin adds a "Sync groups" action for every service that overrides
    # them, and here it would do nothing.

    def sync_nickname(self, user):
        """Main character renamed etc.: recompute the card (emits events).

        AA calls this from *pre_save* receivers, before the new name / corp is
        written and inside AA's own transaction. So the refresh is only
        scheduled (``on_commit``, in its own transaction via ``signals._safe``):
        it then sees the committed data and cannot break AA's transaction.
        """
        try:
            from .signals import schedule_refresh_user

            schedule_refresh_user(getattr(user, "pk", user))
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
