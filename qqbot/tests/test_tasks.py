"""Reconciliation task and the ``qqbot_reconcile`` management command."""

from datetime import timedelta
from io import StringIO
from unittest import mock

from celery import states

from django.core.cache import cache
from django.core.management import call_command
from django.test import TestCase
from django.utils import timezone

from .. import tasks
from ..models import Event
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
        self.assertEqual(result, {"events": 2, "pruned_events": 1, "pruned_codes": 0})
        self.assertFalse(Event.objects.filter(pk=old.pk).exists())
        self.assertEqual(
            sorted(Event.objects.values_list("kind", flat=True)),
            [Event.Kind.CARD, Event.Kind.RECHECK],
        )
        # Second run: nothing changed, nothing written.
        self.assertEqual(tasks.reconcile()["events"], 0)

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

    def test_queue_reconcile_falls_back_inline(self):
        with mock.patch.object(tasks.reconcile, "delay", side_effect=OSError("no broker")), \
                self.assertLogs(tasks.logger, "ERROR"):
            tasks.queue_reconcile()
        self.assertEqual(Event.objects.count(), 2)

    def test_queue_reconcile_queues(self):
        with mock.patch.object(tasks.reconcile, "delay") as delay:
            tasks.queue_reconcile()
        delay.assert_called_once_with()
        self.assertFalse(Event.objects.exists())

    def test_management_command(self):
        out = StringIO()
        call_command("qqbot_reconcile", stdout=out)
        self.assertIn("写出 2 条事件", out.getvalue())
        self.assertEqual(Event.objects.count(), 2)
