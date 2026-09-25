"""Celery tasks (docs/SPEC.md section 7).

Add the daily reconciliation to ``CELERYBEAT_SCHEDULE`` in ``local.py``
(see README.md)::

    CELERYBEAT_SCHEDULE["qqbot_reconcile"] = {
        "task": "qqbot.tasks.reconcile",
        "schedule": crontab(minute="17", hour="4"),
    }
"""

from celery import shared_task

from allianceauth.services.hooks import get_extension_logger
from allianceauth.services.tasks import QueueOnce

from .core import events

logger = get_extension_logger(__name__)


def run_reconcile() -> dict:
    """Recompute every binding and prune old events / codes.

    Returns ``{"events": <events written>, "pruned_events": n, "pruned_codes": n}``.
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


@shared_task(base=QueueOnce, name="qqbot.tasks.reconcile")
def reconcile() -> dict:
    """Daily reconciliation; also queued when the card format changes."""
    return run_reconcile()


def queue_reconcile() -> None:
    """Queue :func:`reconcile`; run it inline if the broker is unreachable."""
    try:
        reconcile.delay()
    except Exception:
        logger.exception("qqbot: could not queue reconcile, running it inline")
        run_reconcile()
