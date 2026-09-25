from datetime import timedelta
from unittest import mock

from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from ..core import codes, events
from ..models import Binding, BindCode, Config, Event, QQGroup
from .utils import add_to_groups, bind, create_group, create_member


def kinds(qq=None):
    qs = Event.objects.order_by("id")
    if qq is not None:
        qs = qs.filter(qq=qq)
    return [e.kind for e in qs]


class EmitTests(TestCase):
    def test_emit(self):
        e = events.emit(Event.Kind.RECHECK, "12345678")
        self.assertEqual((e.kind, e.qq), ("recheck", "12345678"))
        e = events.emit(Event.Kind.RECHECK_ALL)
        self.assertEqual(e.qq, "")

    def test_groups_changed(self):
        events.emit_groups_changed()
        self.assertEqual(kinds(), ["groups", "recheck_all"])


class RefreshBindingTests(TestCase):
    def setUp(self):
        self.group = create_group(100001)
        self.user = create_member("alice")
        self.binding = bind(self.user, "12345678", nickname="n1")

    def refresh(self):
        b = Binding.objects.select_related("user__profile__main_character").get(pk=self.binding.pk)
        return events.refresh_binding(b)

    def test_first_time_writes_both(self):
        self.assertTrue(self.refresh())
        self.assertEqual(kinds(), ["recheck", "card"])
        self.binding.refresh_from_db()
        self.assertEqual(len(self.binding.fingerprint), 64)

    def test_no_change_no_events(self):
        self.refresh()
        Event.objects.all().delete()
        self.assertFalse(self.refresh())
        self.assertEqual(kinds(), [])

    def test_nickname_change_writes_card_only(self):
        self.refresh()
        Event.objects.all().delete()
        Binding.objects.filter(pk=self.binding.pk).update(nickname="n2")
        self.assertTrue(self.refresh())
        self.assertEqual(kinds(), ["card"])
        self.assertEqual(Event.objects.get().qq, "12345678")

    def test_card_override_writes_card_only(self):
        self.refresh()
        Event.objects.all().delete()
        Binding.objects.filter(pk=self.binding.pk).update(card_override="X")
        self.assertTrue(self.refresh())
        self.assertEqual(kinds(), ["card"])

    def test_eligibility_change_writes_recheck_only(self):
        self.refresh()
        Event.objects.all().delete()
        type(self.user).objects.filter(pk=self.user.pk).update(is_active=False)
        self.assertTrue(self.refresh())
        self.assertEqual(kinds(), ["recheck"])

    def test_new_group_writes_recheck(self):
        self.refresh()
        Event.objects.all().delete()
        create_group(100002, kind="role", required=["Cap"])
        self.assertTrue(self.refresh())
        self.assertEqual(kinds(), ["recheck"])
        Event.objects.all().delete()
        add_to_groups(self.user, "Cap")
        self.assertTrue(self.refresh())
        self.assertEqual(kinds(), ["recheck"])

    def test_deactivated_group_writes_recheck(self):
        self.refresh()
        Event.objects.all().delete()
        QQGroup.objects.filter(pk=self.group.pk).update(is_active=False)
        self.assertTrue(self.refresh())
        self.assertEqual(kinds(), ["recheck"])

    def test_qq_change_writes_both_for_new_qq(self):
        self.refresh()
        Event.objects.all().delete()
        Binding.objects.filter(pk=self.binding.pk).update(qq="87654321", verified_qq="87654321")
        self.assertTrue(self.refresh())
        self.assertEqual(kinds("87654321"), ["recheck", "card"])

    def test_refresh_user(self):
        self.assertTrue(events.refresh_user(self.user))
        self.assertFalse(events.refresh_user(self.user.pk))
        Binding.objects.filter(pk=self.binding.pk).update(nickname="n2")
        self.assertTrue(events.refresh_user(self.user.pk))

    def test_refresh_user_without_binding(self):
        other = create_member("bob")
        self.assertFalse(events.refresh_user(other))
        self.assertFalse(events.refresh_user(other.pk))
        self.assertFalse(events.refresh_user(None))
        self.assertEqual(kinds(), [])

    def test_refresh_all(self):
        bind(create_member("bob"), "22345678")
        self.assertEqual(events.refresh_all(), 4)
        self.assertEqual(events.refresh_all(), 0)
        Binding.objects.filter(pk=self.binding.pk).update(nickname="n2")
        self.assertEqual(events.refresh_all(), 1)

    def test_card_format_change_via_refresh_all(self):
        events.refresh_all()
        Event.objects.all().delete()
        config = Config.get_solo()
        config.card_format = "{nickname}"
        config.save()
        self.assertEqual(events.refresh_all(), 1)
        self.assertEqual(kinds(), ["card"])


class RefreshAllBatchTests(TestCase):
    """refresh_all() evaluates bindings in batches: the number of queries does
    not grow with the number of unchanged bindings."""

    def setUp(self):
        self.fixed = create_group(100001)
        self.role = create_group(200001, kind="role", required=["Cap"])
        self.users = []
        for i in range(60):
            user = create_member(f"u{i}", active=(i % 7 != 0))
            if i % 2:
                add_to_groups(user, "Cap")
            status = "trusted" if i % 5 == 0 else "verified"
            bind(user, str(30_000_000 + i), status=status, nickname=f"n{i}",
                 card_override="X" if i % 11 == 0 else "")
            self.users.append(user)
        # a conflict: two trusted bindings on one QQ
        bind(create_member("c1"), "40000000", status="trusted")
        bind(create_member("c2"), "40000000", status="trusted")

    def test_same_fingerprints_as_refresh_binding(self):
        events.refresh_all()
        for b in Binding.objects.select_related("user__profile__main_character"):
            with self.subTest(qq=b.qq):
                self.assertEqual(b.fingerprint, events.compute_fingerprint(b))
                self.assertFalse(events.refresh_binding(b))

    def count(self):
        with CaptureQueriesContext(connection) as ctx:
            self.assertEqual(events.refresh_all(), 0)
        return len(ctx.captured_queries)

    def test_constant_queries_when_nothing_changed(self):
        events.refresh_all()
        small = self.count()
        for i in range(60, 200):
            bind(create_member(f"u{i}"), str(30_000_000 + i))
        events.refresh_all()
        self.assertEqual(self.count(), small)

    def test_changes_detected(self):
        events.refresh_all()
        Event.objects.all().delete()
        Binding.objects.filter(qq="30000001").update(nickname="新")
        type(self.users[3]).objects.filter(pk=self.users[3].pk).update(is_active=False)
        self.assertEqual(events.refresh_all(), 2)
        self.assertEqual(kinds("30000001"), ["card"])
        self.assertEqual(kinds("30000003"), ["recheck"])

    def test_more_than_one_batch(self):
        with mock.patch.object(events, "CHUNK_SIZE", 7):
            n = events.refresh_all()
        self.assertEqual(n, 2 * Binding.objects.count())
        self.assertEqual(events.refresh_all(), 0)


class PollTests(TestCase):
    def setUp(self):
        self.now = timezone.now()

    def ev(self, age_seconds, qq="12345678"):
        e = events.emit(Event.Kind.RECHECK, qq)
        Event.objects.filter(pk=e.pk).update(created_at=self.now - timedelta(seconds=age_seconds))
        return e.pk

    def test_empty(self):
        page = events.poll(0, now=self.now)
        self.assertEqual((page.events, page.last_id, page.has_more), ([], 0, False))
        page = events.poll(42, now=self.now)
        self.assertEqual(page.last_id, 42)

    def test_only_old_enough_events(self):
        old1 = self.ev(60)
        old2 = self.ev(11)
        self.ev(1)  # may belong to a transaction that has not committed yet
        page = events.poll(0, now=self.now)
        self.assertEqual([e.pk for e in page.events], [old1, old2])
        self.assertEqual((page.last_id, page.has_more), (old2, False))
        later = events.poll(page.last_id, now=self.now + timedelta(seconds=10))
        self.assertEqual(len(later.events), 1)

    def test_stops_before_first_young_event(self):
        """Ids and created_at may disagree a little (several processes); the
        cursor must never jump over a young, lower id."""
        first = self.ev(60)
        self.ev(2)
        self.ev(30)  # higher id but already old
        page = events.poll(0, now=self.now)
        self.assertEqual([e.pk for e in page.events], [first])
        self.assertEqual(page.last_id, first)

    def test_out_of_order_commit_is_not_skipped(self):
        """An event with a lower id that becomes visible after a higher one
        (its transaction committed later) is still delivered."""
        low = self.ev(3)    # inserted first, by a transaction still running
        high = self.ev(3)   # inserted later, committed first
        page = events.poll(0, now=self.now)
        self.assertEqual(page.events, [])
        page = events.poll(0, now=self.now + timedelta(seconds=10))
        self.assertEqual([e.pk for e in page.events], [low, high])

    def test_limit_and_has_more(self):
        ids = [self.ev(60) for _ in range(5)]
        page = events.poll(0, limit=2, now=self.now)
        self.assertEqual([e.pk for e in page.events], ids[:2])
        self.assertEqual((page.last_id, page.has_more), (ids[1], True))
        page = events.poll(page.last_id, limit=3, now=self.now)
        self.assertEqual([e.pk for e in page.events], ids[2:])
        self.assertFalse(page.has_more)

    def test_has_more_ignores_young_events(self):
        ids = [self.ev(60) for _ in range(2)]
        self.ev(1)
        page = events.poll(0, limit=2, now=self.now)
        self.assertEqual([e.pk for e in page.events], ids)
        self.assertFalse(page.has_more)


class PruneTests(TestCase):
    def test_prune(self):
        now = timezone.now()
        user = create_member("alice")
        old_event = events.emit(Event.Kind.RECHECK, "12345678")
        Event.objects.filter(pk=old_event.pk).update(created_at=now - timedelta(days=31))
        new_event = events.emit(Event.Kind.RECHECK, "12345678")
        Event.objects.filter(pk=new_event.pk).update(created_at=now - timedelta(days=29))

        def code(**kw):
            return BindCode.objects.create(
                user=user, qq="12345678", nickname="n",
                code_hash=codes.hash_code(codes.generate_code()), **kw
            )

        old_expired = code(expires_at=now - timedelta(days=8))
        recent_expired = code(expires_at=now - timedelta(days=6))
        old_used = code(expires_at=now - timedelta(days=6), used_at=now - timedelta(days=8))
        old_invalidated = code(expires_at=now - timedelta(days=6),
                               invalidated_at=now - timedelta(days=8))
        live = code(expires_at=now + timedelta(minutes=5))

        result = events.prune(now=now)
        self.assertEqual(result, {"events": 1, "codes": 3})
        self.assertEqual(list(Event.objects.values_list("pk", flat=True)), [new_event.pk])
        remaining = set(BindCode.objects.values_list("pk", flat=True))
        self.assertEqual(remaining, {recent_expired.pk, live.pk})
        self.assertFalse({old_expired.pk, old_used.pk, old_invalidated.pk} & remaining)
