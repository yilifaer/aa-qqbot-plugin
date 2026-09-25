"""``python manage.py qqbot_reconcile``: run the daily reconciliation now.

``python manage.py qqbot_reconcile``：立即执行一次每日对账。
"""

from django.core.management.base import BaseCommand

from ...tasks import run_reconcile


class Command(BaseCommand):
    # Bilingual plain strings (English first): management commands run in
    # English, the Chinese is kept for Chinese-speaking operators.
    # 中英双语的普通字符串（英文在前）：管理命令按英文运行，中文留给中文运维看。
    help = (
        "Recompute the group decisions and group nicknames of every QQ binding, and prune "
        "old events and verification codes (same as the daily scheduled task). / "
        "重新计算所有 QQ 绑定的入群判定和群名片，并清理过期的事件和验证码（与每日定时任务相同）。"
    )

    def handle(self, *args, **options):
        result = run_reconcile()
        self.stdout.write(
            self.style.SUCCESS(
                (
                    "Reconciliation done: wrote {events} events, pruned {pruned_events} old events "
                    "and {pruned_codes} old verification codes.\n"
                    "对账完成：写出 {events} 条事件，清理 {pruned_events} 条旧事件、"
                    "{pruned_codes} 个旧验证码。"
                ).format(**result)
            )
        )
