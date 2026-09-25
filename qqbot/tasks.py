"""Celery tasks (docs/SPEC.md section 7).

Add the daily reconciliation to ``CELERYBEAT_SCHEDULE`` in ``local.py``
(see README.md)::

    CELERYBEAT_SCHEDULE["qqbot_reconcile"] = {
        "task": "qqbot.tasks.reconcile",
        "schedule": crontab(minute="17", hour="4"),
    }

Celery 任务（见 docs/SPEC.md 第 7 节）。

请在 ``local.py`` 的 ``CELERYBEAT_SCHEDULE`` 里加上每日对账任务
（见 README.md），写法见上面的示例。
"""

from celery import shared_task

from allianceauth.services.hooks import get_extension_logger
from allianceauth.services.tasks import QueueOnce

from .core import events

logger = get_extension_logger(__name__)


def run_reconcile() -> dict:
    """Recompute every binding and prune old events / codes.

    Returns ``{"events": <events written>, "pruned_events": n, "pruned_codes": n}``.

    重新计算所有绑定，并清理旧的事件和验证码。

    返回 ``{"events": <写出的事件数>, "pruned_events": n, "pruned_codes": n}``。
    """
    written = events.refresh_all()
    pruned = events.prune()
    result = {
        "events": written,
        "pruned_events": pruned.get("events", 0),
        "pruned_codes": pruned.get("codes", 0),
    }
    logger.info(
        "qqbot reconcile: %(events)d events written, %(pruned_events)d old events "
        "and %(pruned_codes)d old codes pruned",
        result,
    )
    return result


# unlock_before_run: the QueueOnce lock only stops duplicate *queued* runs.
# A card-format change saved while a run is already going must queue a new
# run (that one reads the new format); with the default lock held for the
# whole run, that second queueing was silently rejected.
# unlock_before_run：QueueOnce 的锁只用来阻止重复“排队”的任务。
# 如果一次对账正在运行时有人保存了新的群名片格式，必须能再排上一次新的对账
# （新的那次会读到新格式）；如果用默认设置，锁会在整个运行期间一直被占着，
# 第二次排队就会被悄悄拒掉。
@shared_task(
    base=QueueOnce,
    name="qqbot.tasks.reconcile",
    once={"graceful": True, "unlock_before_run": True},
)
def reconcile() -> dict:
    """Daily reconciliation; also queued when the card format changes.

    每日对账；群名片格式改变时也会排队执行一次。
    """
    return run_reconcile()


def queue_reconcile() -> None:
    """Queue :func:`reconcile`; run it inline if the broker is unreachable.

    把 ``reconcile`` 放进任务队列；如果连不上消息队列（broker），就直接在当前进程里执行。
    """
    try:
        reconcile.delay()
    except Exception:
        logger.exception("qqbot: could not queue reconcile, running it inline")
        run_reconcile()
