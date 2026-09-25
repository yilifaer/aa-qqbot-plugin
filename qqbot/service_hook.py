"""The "QQ 绑定" card on AA's services page (docs/SPEC.md section 5).

Members do everything inside this card (DECISIONS.md #18): bind, see the
code and the groups, change the nickname, re-bind, unbind. The card's
context comes from ``views.member.card_context``. AA's service callbacks
(``validate_user``, ``delete_user``, ...) do nothing: correctness comes from
the signal handlers and the daily reconciliation, which judge every binding
from scratch anyway.

AA 服务页上的「QQ 绑定」卡片（docs/SPEC.md 第 5 节）。

成员的所有操作都在这张卡片里完成（DECISIONS.md #18）：绑定、查看验证码和
群、改昵称、换绑、解绑。卡片的数据来自 ``views.member.card_context``。
AA 的服务回调（``validate_user``、``delete_user`` 等）什么都不做：正确性由
信号处理函数和每天的对账保证，它们本来就会从头判断每一个绑定。
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
    # AA 回调：故意留空，什么都不做

    def validate_user(self, user):
        pass

    def delete_user(self, user, notify_user=False):
        return False

    # update_groups / update_all_groups are deliberately NOT overridden: AA's
    # User admin adds a "Sync groups" action for every service that overrides
    # them, and here it would do nothing.
    # 故意不重写 update_groups / update_all_groups：服务只要重写了它们，AA 的
    # 用户后台就会为它加一个「Sync groups」操作，而在这里这个操作什么也不做。

    def sync_nickname(self, user):
        """Main character renamed etc.: recompute the card (emits events).

        AA calls this from *pre_save* receivers, before the new name / corp is
        written and inside AA's own transaction. So the refresh is only
        scheduled (``on_commit``, in its own transaction via ``signals._safe``):
        it then sees the committed data and cannot break AA's transaction.

        主角色改名等情况：重新计算群名片（会发出事件）。

        AA 是在 *pre_save* 接收器里调用这个方法的，这时新的名字 / 军团还没
        写入，而且还处在 AA 自己的事务里。所以这里只是安排刷新（``on_commit``，
        并通过 ``signals._safe`` 在独立事务中执行）：这样刷新时看到的是已提交
        的数据，也不会弄坏 AA 的事务。
        """
        try:
            from .signals import schedule_refresh_user

            schedule_refresh_user(getattr(user, "pk", user))
        except Exception:
            logger.exception("qqbot: sync_nickname failed for %s", user)

    # Card ---------------------------------------------------------------------
    # 卡片

    def render_services_ctrl(self, request):
        from .views.member import card_context

        context = card_context(request)
        context["service_name"] = self.title
        return render_to_string(self.service_ctrl_template, context, request=request)
