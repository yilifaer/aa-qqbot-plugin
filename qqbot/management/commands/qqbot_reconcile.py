"""``python manage.py qqbot_reconcile``: run the daily reconciliation now.

``python manage.py qqbot_reconcile``：立即执行一次每日对账。
"""

from django.core.management.base import BaseCommand

from ...tasks import run_reconcile


class Command(BaseCommand):
    help = "重新计算所有 QQ 绑定的入群判定和群名片，并清理过期的事件和验证码（与每日定时任务相同）。"

    def handle(self, *args, **options):
        result = run_reconcile()
        self.stdout.write(
            self.style.SUCCESS(
                "对账完成：写出 {events} 条事件，清理 {pruned_events} 条旧事件、"
                "{pruned_codes} 个旧验证码。".format(**result)
            )
        )
