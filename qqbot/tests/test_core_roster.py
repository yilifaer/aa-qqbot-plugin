from datetime import timedelta

from django.test import TestCase
from django.utils import timezone

from ..core.roster import in_fresh_roster, unbound_roster, update_roster
from ..models import Config, QQGroup, RosterEntry
from .utils import bind, create_group, create_member, put_in_roster


def roster(group):
    return set(RosterEntry.objects.filter(group=group).values_list("qq", flat=True))


class UpdateRosterTests(TestCase):
    def setUp(self):
        self.group = create_group(100001)

    def test_replace(self):
        t1 = timezone.now() - timedelta(hours=1)
        update_roster(self.group, ["11111111", "22222222"], now=t1)
        self.assertEqual(roster(self.group), {"11111111", "22222222"})
        t2 = timezone.now()
        result = update_roster(self.group, ["22222222", "33333333"], now=t2)
        self.assertEqual(result, {"added": 1, "removed": 1, "total": 2})
        self.assertEqual(roster(self.group), {"22222222", "33333333"})
        self.assertEqual(
            set(RosterEntry.objects.filter(group=self.group).values_list("seen_at", flat=True)), {t2}
        )
        self.group.refresh_from_db()
        self.assertEqual(self.group.last_roster_at, t2)

    def test_sets_attribute_on_instance(self):
        now = timezone.now()
        update_roster(self.group, ["11111111"], now=now)
        self.assertEqual(self.group.last_roster_at, now)

    def test_invalid_dropped_and_normalized(self):
        update_roster(self.group, ["11111111", "bad", "0123456", "１２３４５６７８", 22222222, None, "11111111"])
        self.assertEqual(roster(self.group), {"11111111", "12345678", "22222222"})

    def test_empty_list_clears(self):
        update_roster(self.group, ["11111111"])
        update_roster(self.group, [])
        self.assertEqual(roster(self.group), set())
        self.group.refresh_from_db()
        self.assertIsNotNone(self.group.last_roster_at)

    def test_other_groups_untouched(self):
        other = create_group(100002)
        update_roster(other, ["11111111"])
        update_roster(self.group, ["22222222"])
        update_roster(self.group, [])
        self.assertEqual(roster(other), {"11111111"})

    def test_large(self):
        first = [str(10_000_000 + i) for i in range(1200)]
        update_roster(self.group, first)
        second = [str(10_000_000 + i) for i in range(600, 1800)]
        update_roster(self.group, second)
        self.assertEqual(roster(self.group), set(second))


class InFreshRosterTests(TestCase):
    def setUp(self):
        self.group = create_group(100001)
        self.config = Config.get_solo()

    def test_fresh(self):
        update_roster(self.group, ["11111111"])
        self.assertTrue(in_fresh_roster("11111111"))
        self.assertFalse(in_fresh_roster("22222222"))

    def test_normalizes_input(self):
        update_roster(self.group, ["11111111"])
        self.assertTrue(in_fresh_roster(" 11111111 "))
        self.assertFalse(in_fresh_roster("bad"))
        self.assertFalse(in_fresh_roster(None))

    def test_stale(self):
        now = timezone.now()
        update_roster(self.group, ["11111111"], now=now - timedelta(days=7, seconds=1))
        self.assertFalse(in_fresh_roster("11111111", now=now))

    def test_boundary(self):
        now = timezone.now()
        update_roster(self.group, ["11111111"], now=now - timedelta(days=7))
        self.assertTrue(in_fresh_roster("11111111", now=now))

    def test_uses_config(self):
        now = timezone.now()
        update_roster(self.group, ["11111111"], now=now - timedelta(days=3))
        self.assertTrue(in_fresh_roster("11111111", now=now))
        self.config.roster_max_age_days = 2
        self.config.save()
        self.assertFalse(in_fresh_roster("11111111", now=now))

    def test_inactive_group(self):
        update_roster(self.group, ["11111111"])
        QQGroup.objects.filter(pk=self.group.pk).update(is_active=False)
        self.assertFalse(in_fresh_roster("11111111"))

    def test_never_reported(self):
        RosterEntry.objects.create(group=self.group, qq="11111111", seen_at=timezone.now())
        self.assertFalse(in_fresh_roster("11111111"))

    def test_any_group_counts(self):
        stale = create_group(100002)
        now = timezone.now()
        update_roster(stale, ["11111111"], now=now - timedelta(days=30))
        self.assertFalse(in_fresh_roster("11111111", now=now))
        update_roster(self.group, ["11111111"], now=now)
        self.assertTrue(in_fresh_roster("11111111", now=now))


class UnboundRosterTests(TestCase):
    def test_unbound(self):
        g1 = create_group(100001)
        g2 = create_group(100002)
        g3 = create_group(100003, is_active=False)
        put_in_roster(g1, ["11111111", "22222222", "33333333"])
        put_in_roster(g2, ["22222222", "44444444"])
        put_in_roster(g3, ["55555555"])
        bind(create_member("a"), "11111111")
        bind(create_member("b"), "33333333", status="trusted")
        result = [(e.group.group_id, e.qq) for e in unbound_roster()]
        self.assertEqual(sorted(result), [("100001", "22222222"), ("100002", "22222222"),
                                          ("100002", "44444444")])
        self.assertEqual([e.qq for e in unbound_roster(g2)], ["22222222", "44444444"])
        self.assertEqual(list(unbound_roster(g3)), [])
