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
    """Recompute every binding and prune old events / codes (and old audit
    log entries when ``QQBOT_AUDIT_RETENTION_DAYS`` is set).

    Returns ``{"events": <events written>, "pruned_events": n, "pruned_codes": n,
    "pruned_audit": n}``.

    重新计算所有绑定，并清理旧的事件和验证码（设置了
    ``QQBOT_AUDIT_RETENTION_DAYS`` 时还会清理旧的审计日志）。

    返回 ``{"events": <写出的事件数>, "pruned_events": n, "pruned_codes": n,
    "pruned_audit": n}``。
    """
    written = events.refresh_all()
    pruned = events.prune()
    result = {
        "events": written,
        "pruned_events": pruned.get("events", 0),
        "pruned_codes": pruned.get("codes", 0),
        "pruned_audit": pruned.get("audit", 0),
    }
    logger.info(
        "qqbot reconcile: %(events)d events written, %(pruned_events)d old events, "
        "%(pruned_codes)d old codes and %(pruned_audit)d old audit log entries pruned",
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


def queue_reconcile() -> bool:
    """Queue :func:`reconcile`. Returns True when it was queued (or a queued
    run that has not started yet was already waiting), False otherwise.

    Nothing runs in the calling process: if the broker is unreachable, an
    error is logged and the group nicknames are recalculated by the next
    scheduled reconcile or by ``python manage.py qqbot_reconcile``.
    celery-once takes its lock before publishing and keeps it when publishing
    fails (one hour by default), so the lock is released here; otherwise
    every later attempt in that hour would be rejected without a word.

    把 ``reconcile`` 放进任务队列。成功放进队列（或者已经有一次还没开始的
    对账在排队）时返回 True，否则返回 False。

    不会在当前进程里执行对账：如果连不上消息队列（broker），只记一条错误日志，
    群名片会在下一次定时对账、或者手动运行 ``python manage.py qqbot_reconcile``
    时重新计算。celery-once 在发布任务之前就加锁，发布失败时也不会释放这把锁
    （默认保留一小时），所以这里要手动释放；不然这一小时里之后的每次排队都会
    被悄悄拒掉。
    """
    try:
        reconcile.delay()
    except Exception:
        logger.exception(
            "qqbot: could not queue reconcile; the group nicknames will be recalculated by "
            "the next scheduled reconcile or by `python manage.py qqbot_reconcile`"
        )
        _release_reconcile_lock()
        return False
    return True


def _release_reconcile_lock() -> None:
    """Release the celery-once lock that a failed ``reconcile.delay()`` left
    behind. A failure is logged, never raised.

    释放 ``reconcile.delay()`` 失败后留下的 celery-once 锁。出错只记日志，
    不抛出异常。
    """
    try:
        # delay() calls apply_async(args=(), kwargs={}), which builds the
        # lock key from exactly these arguments.
        # delay() 调用的是 apply_async(args=(), kwargs={})，锁的键就是用这两个
        # 参数生成的。
        reconcile.once_backend.clear_lock(reconcile.get_key(args=(), kwargs={}))
    except Exception:
        logger.exception("qqbot: could not release the reconcile queue lock")
