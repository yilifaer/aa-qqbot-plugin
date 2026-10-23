"""Reconciliation task and the ``qqbot_reconcile`` management command."""

from datetime import timedelta
from io import StringIO
from unittest import mock

from celery import states
from celery.app.task import Task

from django.core.cache import cache
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.utils import timezone

from .. import tasks
from ..models import AuditLog, Event
from .utils import bind, create_group, create_member

QQ = "12345678"


class ReconcileTests(TestCase):
    def setUp(self):
        create_group(100001)
        self.user = create_member("alice")
        bind(self.user, QQ)

    def test_task_refreshes_and_prunes(self):
        old = Event.objects.create(kind=Event.Kind.RECHECK, qq="99999")
        Event.objects.filter(pk=old.pk).update(created_at=timezone.now() - timedelta(days=40))
        result = tasks.reconcile()
        self.assertEqual(
            result, {"events": 2, "pruned_events": 1, "pruned_codes": 0, "pruned_audit": 0}
        )
        self.assertFalse(Event.objects.filter(pk=old.pk).exists())
        self.assertEqual(
            sorted(Event.objects.values_list("kind", flat=True)),
            [Event.Kind.CARD, Event.Kind.RECHECK],
        )
        # Second run: nothing changed, nothing written.
        self.assertEqual(tasks.reconcile()["events"], 0)

    def test_task_prunes_audit_log_when_retention_set(self):
        now = timezone.now()
        old = AuditLog.objects.create(action=AuditLog.Action.BIND, qq=QQ)
        recent = AuditLog.objects.create(action=AuditLog.Action.BIND, qq=QQ)
        AuditLog.objects.filter(pk=old.pk).update(created_at=now - timedelta(days=61))
        AuditLog.objects.filter(pk=recent.pk).update(created_at=now - timedelta(days=59))
        # default: keep forever / 默认：永久保留
        self.assertEqual(tasks.run_reconcile()["pruned_audit"], 0)
        with override_settings(QQBOT_AUDIT_RETENTION_DAYS=60), \
                self.assertLogs(tasks.logger, "INFO") as logs:
            result = tasks.run_reconcile()
        self.assertEqual(result["pruned_audit"], 1)
        self.assertIn("1 old audit log entries pruned", logs.output[-1])
        self.assertEqual(list(AuditLog.objects.values_list("pk", flat=True)), [recent.pk])

    def test_task_registration(self):
        self.assertEqual(tasks.reconcile.name, "qqbot.tasks.reconcile")
        from allianceauth.services.tasks import QueueOnce

        self.assertIsInstance(tasks.reconcile, QueueOnce)

    def test_change_during_run_queues_another_run(self):
        """A card-format change saved while reconcile runs must get its own
        run; QueueOnce used to reject it silently while the lock was held."""
        cache.clear()
        inner = []
        started = []

        def fake_run():
            if not started:  # e.g. the settings page saving a new card format
                started.append(True)
                # Eager mode: the nested run happens right here.
                inner.append(tasks.reconcile.delay())
            return {}

        with mock.patch.object(tasks, "run_reconcile", side_effect=fake_run) as run:
            tasks.reconcile.delay()
        self.assertEqual(len(inner), 1)
        self.assertNotEqual(inner[0].state, states.REJECTED)
        self.assertEqual(run.call_count, 2)

    def test_task_apply(self):
        result = tasks.reconcile.apply()
        self.assertTrue(result.successful())
        self.assertEqual(result.result["events"], 2)

    def test_queue_reconcile_broker_down(self):
        """Publishing fails after celery-once took its lock: nothing runs in
        the request, an error is logged and the lock is released, so the
        next attempt (broker back) is not rejected for an hour.

        celery-once 已经加锁之后发布失败：请求里不执行对账，记一条错误日志并
        释放锁，这样下一次排队（broker 恢复后）不会在一小时内被拒掉。"""
        cache.clear()
        key = tasks.reconcile.get_key(args=(), kwargs={})
        held = []

        def publish(*args, **kwargs):
            held.append(cache.get(key))
            raise OSError("no broker")

        with mock.patch.object(Task, "apply_async", side_effect=publish), \
                self.assertLogs(tasks.logger, "ERROR") as logs:
            self.assertIs(tasks.queue_reconcile(), False)
        # celery-once locked before publishing / celery-once 在发布之前就加了锁
        self.assertEqual(held, ["lock"])
        self.assertIn("python manage.py qqbot_reconcile", logs.output[0])
        # not run inline / 没有在当前进程里执行
        self.assertFalse(Event.objects.exists())
        self.assertIsNone(cache.get(key))
        # Broker back: queued (and, in eager mode, run) instead of rejected.
        # broker 恢复后：任务排上队（eager 模式下直接执行），而不是被拒掉。
        self.assertIs(tasks.queue_reconcile(), True)
        self.assertEqual(Event.objects.count(), 2)

    def test_queue_reconcile_lock_release_failure_is_logged(self):
        with mock.patch.object(tasks.reconcile, "delay", side_effect=OSError("no broker")), \
                mock.patch(
                    "allianceauth.services.tasks.DjangoBackend.clear_lock",
                    side_effect=OSError("no cache"),
                ) as clear, \
                self.assertLogs(tasks.logger, "ERROR") as logs:
            self.assertIs(tasks.queue_reconcile(), False)
        clear.assert_called_once_with(tasks.reconcile.get_key(args=(), kwargs={}))
        self.assertEqual(len(logs.output), 2)
        self.assertIn("could not release", logs.output[1])
        self.assertFalse(Event.objects.exists())

    def test_queue_reconcile_queues(self):
        with mock.patch.object(tasks.reconcile, "delay") as delay:
            self.assertIs(tasks.queue_reconcile(), True)
        delay.assert_called_once_with()
        self.assertFalse(Event.objects.exists())

    def test_management_command(self):
        out = StringIO()
        call_command("qqbot_reconcile", stdout=out)
        self.assertIn("写出 2 条事件", out.getvalue())
        self.assertEqual(Event.objects.count(), 2)
