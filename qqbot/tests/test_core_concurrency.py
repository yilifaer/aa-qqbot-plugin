"""Concurrent binding writes against a real database.

SQLite (the default test database) ignores row locks and serializes writers,
so these tests only run on PostgreSQL or MySQL/MariaDB, e.g.::

    DJANGO_SETTINGS_MODULE=<settings with a postgres/mysql DATABASES> \\
        python manage.py test qqbot.tests.test_core_concurrency

Each test forces the dangerous interleaving: the first thread pauses inside
its transaction (after taking its locks), and the second thread starts
during that pause. With correct locking the second thread waits and the
result equals some serial order.
"""

import threading
import time
from unittest import skipUnless

from django.core.cache import cache
from django.db import connection, connections
from django.test import TransactionTestCase

from ..core import bindings, locks
from ..models import AuditLog, Binding, BindCode
from .utils import create_group, create_member, put_in_roster

PAUSE = 0.5  # seconds the first thread holds its transaction open
REAL_DB = connection.vendor in ("postgresql", "mysql")


@skipUnless(REAL_DB, "needs a database with row locks (PostgreSQL or MySQL/MariaDB)")
class ConcurrentBindingTests(TransactionTestCase):

    def setUp(self):
        cache.clear()
        locks.ensure_rows()  # TransactionTestCase flushes the migration's rows
        self.group = create_group(100001)
        self.alice = create_member("alice")
        self.bob = create_member("bob")

    def race(self, first, second, pause_in="_user_binding_for_update"):
        """Run ``first`` and ``second`` in two threads; ``first`` pauses right
        after ``bindings.<pause_in>`` returns, and ``second`` starts then."""
        entered = threading.Event()
        real = getattr(bindings, pause_in)

        def paused(*args, **kwargs):
            result = real(*args, **kwargs)
            if threading.current_thread().name == "first" and not entered.is_set():
                entered.set()
                time.sleep(PAUSE)
            return result

        results = {}

        def run(name, fn):
            try:
                results[name] = ("ok", fn())
            except Exception as exc:  # noqa: BLE001 - reported by the test
                results[name] = ("error", repr(exc))
            finally:
                connections.close_all()

        setattr(bindings, pause_in, paused)
        try:
            t1 = threading.Thread(target=run, args=("first", first), name="first")
            t1.start()
            entered.wait(10)
            t2 = threading.Thread(target=run, args=("second", second), name="second")
            t2.start()
            t1.join(30)
            t2.join(30)
        finally:
            setattr(bindings, pause_in, real)
        for name, (status, value) in results.items():
            self.assertEqual(status, "ok", f"{name}: {value}")
        return results["first"][1], results["second"][1]

    def test_two_users_claim_the_same_qq(self):
        ra = bindings.submit(self.alice, "12345678", "a")
        rb = bindings.submit(self.bob, "12345678", "b")
        first, second = self.race(lambda: bindings.claim("12345678", ra.code),
                                  lambda: bindings.claim("12345678", rb.code))
        self.assertEqual((first.outcome, second.outcome), ("claimed", "claimed"))
        # the later claim took over, as in the serial order
        self.assertEqual(list(Binding.objects.values_list("user__username", "qq", "status")),
                         [("bob", "12345678", "verified")])

    def test_submit_while_claiming_own_code_does_not_deadlock(self):
        Binding.objects.create(user=self.alice, qq="11111111", nickname="old",
                               status="verified", verified_via="code")
        r = bindings.submit(self.alice, "12345678", "a")
        first, second = self.race(lambda: bindings.submit(self.alice, "22345678", "a"),
                                  lambda: bindings.claim("12345678", r.code))
        self.assertEqual(first.outcome, "pending")
        self.assertEqual(second.outcome, "code_used")  # invalidated by the new submit
        self.assertEqual(Binding.objects.get(user=self.alice).qq, "11111111")

    def test_claim_while_submitting_does_not_deadlock(self):
        Binding.objects.create(user=self.alice, qq="11111111", nickname="old",
                               status="verified", verified_via="code")
        r = bindings.submit(self.alice, "12345678", "a")
        first, second = self.race(lambda: bindings.claim("12345678", r.code),
                                  lambda: bindings.submit(self.alice, "22345678", "a"))
        self.assertEqual(first.outcome, "claimed")
        self.assertEqual(second.outcome, "cooldown")  # the claim just changed the QQ

    def test_trusted_submit_racing_a_claim_gets_taken(self):
        ra = bindings.submit(self.alice, "12345678", "a")
        put_in_roster(self.group, ["12345678"])
        first, second = self.race(lambda: bindings.claim("12345678", ra.code),
                                  lambda: bindings.submit(self.bob, "12345678", "b"))
        self.assertEqual((first.outcome, second.outcome), ("claimed", "taken"))
        self.assertEqual(list(Binding.objects.values_list("user__username", "status")),
                         [("alice", "verified")])

    def test_two_trusted_submits_on_one_qq_report_the_conflict(self):
        put_in_roster(self.group, ["12345678"])
        first, second = self.race(lambda: bindings.submit(self.alice, "12345678", "a"),
                                  lambda: bindings.submit(self.bob, "12345678", "b"))
        self.assertEqual((first.outcome, second.outcome), ("trusted", "conflict"))
        self.assertEqual(AuditLog.objects.filter(action=AuditLog.Action.CONFLICT).count(), 1)
        self.assertEqual(len(bindings.conflicts()), 1)

    def test_double_submit_without_binding_trusted(self):
        put_in_roster(self.group, ["12345678"])
        first, second = self.race(lambda: bindings.submit(self.alice, "12345678", "a"),
                                  lambda: bindings.submit(self.alice, "12345678", "a"))
        self.assertEqual((first.outcome, second.outcome), ("trusted", "unchanged"))
        self.assertEqual(Binding.objects.count(), 1)

    def test_double_submit_without_binding_pending(self):
        first, second = self.race(lambda: bindings.submit(self.alice, "12345678", "a"),
                                  lambda: bindings.submit(self.alice, "22345678", "a"),
                                  pause_in="_invalidate_codes")
        self.assertEqual((first.outcome, second.outcome), ("pending", "pending"))
        live = BindCode.objects.filter(user=self.alice, used_at__isnull=True,
                                       invalidated_at__isnull=True)
        self.assertEqual(list(live.values_list("qq", flat=True)), ["22345678"])
        self.assertEqual(bindings.claim("12345678", first.code).outcome, "code_used")
