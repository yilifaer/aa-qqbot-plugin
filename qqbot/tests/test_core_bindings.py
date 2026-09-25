from datetime import timedelta
from unittest import mock

from django.core.cache import cache
from django.db import IntegrityError, transaction
from django.db.models import UniqueConstraint
from django.db.models.query import QuerySet
from django.test import TestCase
from django.utils import timezone

from ..core import bindings, codes, locks
from ..core import eligibility as el
from ..models import AuditLog, Binding, BindCode, Config, Event, Lock
from .utils import bind, create_group, create_member, put_in_roster

A = AuditLog.Action


def event_kinds(qq):
    return [e.kind for e in Event.objects.filter(qq=qq).order_by("id")]


def audits(action, **kw):
    return AuditLog.objects.filter(action=action, **kw)


class BaseTestCase(TestCase):
    def setUp(self):
        cache.clear()
        self.config = Config.get_solo()
        self.group = create_group(100001)
        self.user = create_member("alice", character_name="Kaela Voss")

    def fresh(self, pk):
        return Binding.objects.get(pk=pk)


class SubmitValidationTests(BaseTestCase):
    def test_invalid_qq(self):
        for qq in ["", "abc", "0123456", "1234", "123456789012", None]:
            with self.subTest(qq=qq):
                r = bindings.submit(self.user, qq, "凯拉")
                self.assertFalse(r.ok)
                self.assertEqual(r.outcome, "invalid")
                self.assertTrue(r.message)
        self.assertFalse(Binding.objects.exists())
        self.assertFalse(BindCode.objects.exists())

    def test_invalid_nickname(self):
        r = bindings.submit(self.user, "12345678", "<script>")
        self.assertEqual((r.ok, r.outcome), (False, "invalid"))
        self.assertIn("昵称", r.message)

    def test_normalizes_qq_and_nickname(self):
        put_in_roster(self.group, ["12345678"])
        r = bindings.submit(self.user, " １２３４５６７８ ", "  凯拉 ")
        self.assertEqual(r.outcome, "trusted")
        b = Binding.objects.get(user=self.user)
        self.assertEqual((b.qq, b.nickname), ("12345678", "凯拉"))


class SubmitTrustedTests(BaseTestCase):
    def setUp(self):
        super().setUp()
        put_in_roster(self.group, ["12345678", "22345678"])

    def test_new_binding(self):
        now = timezone.now()
        r = bindings.submit(self.user, "12345678", "凯拉", now=now)
        self.assertTrue(r.ok)
        self.assertEqual(r.outcome, "trusted")
        self.assertIsNone(r.code)
        b = Binding.objects.get(user=self.user)
        self.assertEqual(r.binding.pk, b.pk)
        self.assertEqual(b.status, "trusted")
        self.assertEqual(b.verified_via, "")
        self.assertIsNone(b.verified_at)
        self.assertEqual(b.qq_changed_at, now)
        self.assertEqual(audits(A.BIND, qq="12345678").count(), 1)
        self.assertIn("recheck", event_kinds("12345678"))
        self.assertIn("card", event_kinds("12345678"))
        self.assertEqual(el.evaluate(self.group, ["12345678"])["12345678"].decision, el.ALLOW)

    def test_invalidates_live_codes(self):
        r = bindings.submit(self.user, "99999999", "凯拉")
        self.assertEqual(r.outcome, "pending")
        bindings.submit(self.user, "12345678", "凯拉")
        self.assertIsNone(bindings.live_code(self.user))
        self.assertIsNotNone(BindCode.objects.get().invalidated_at)

    def test_rebind(self):
        t0 = timezone.now() - timedelta(days=3)
        bindings.submit(self.user, "12345678", "凯拉", now=t0)
        b = Binding.objects.get(user=self.user)
        b.card_override = "特别名片"
        b.status = "verified"
        b.verified_via = "code"
        b.verified_at = t0
        b.save()
        Event.objects.all().delete()
        now = timezone.now()
        r = bindings.submit(self.user, "22345678", "凯拉2", now=now)
        self.assertEqual(r.outcome, "trusted")
        b = self.fresh(b.pk)
        self.assertEqual((b.qq, b.nickname, b.status, b.verified_via), ("22345678", "凯拉2", "trusted", ""))
        self.assertIsNone(b.verified_at)
        self.assertEqual(b.card_override, "")
        self.assertEqual(b.qq_changed_at, now)
        self.assertIn("recheck", event_kinds("12345678"))
        self.assertIn("recheck", event_kinds("22345678"))
        rebind = audits(A.REBIND).get()
        self.assertEqual(rebind.qq, "22345678")
        self.assertEqual(rebind.detail["old_qq"], "12345678")
        self.assertEqual(el.evaluate(self.group, ["12345678"])["12345678"].reason, el.NOT_BOUND)

    def test_conflict(self):
        bob = create_member("bob")
        bindings.submit(bob, "12345678", "bob")
        r = bindings.submit(self.user, "12345678", "凯拉")
        self.assertTrue(r.ok)
        self.assertEqual(r.outcome, "conflict")
        self.assertEqual(Binding.objects.filter(qq="12345678", status="trusted").count(), 2)
        conflict = audits(A.CONFLICT).get()
        self.assertEqual(conflict.target_name, "alice")
        self.assertEqual(conflict.detail["others"], ["bob"])
        d = el.evaluate(self.group, ["12345678"])["12345678"]
        self.assertEqual((d.decision, d.reason), (el.REVIEW, el.CONFLICT))
        self.assertEqual([qq for qq, _ in bindings.conflicts()], ["12345678"])

    def test_stale_roster_goes_pending(self):
        now = timezone.now() + timedelta(days=8)
        r = bindings.submit(self.user, "12345678", "凯拉", now=now)
        self.assertEqual(r.outcome, "pending")
        self.assertFalse(Binding.objects.exists())

    def test_inactive_group_roster_goes_pending(self):
        self.group.is_active = False
        self.group.save()
        r = bindings.submit(self.user, "12345678", "凯拉")
        self.assertEqual(r.outcome, "pending")


class SubmitPendingTests(BaseTestCase):
    def test_pending(self):
        now = timezone.now()
        r = bindings.submit(self.user, "12345678", "凯拉", now=now)
        self.assertTrue(r.ok)
        self.assertEqual(r.outcome, "pending")
        self.assertRegex(r.code, r"^QQ-[23456789ABCDEFGHJKMNPQRSTUVWXYZ]{6}$")
        self.assertEqual(r.expires_at, now + timedelta(minutes=self.config.code_ttl_minutes))
        bc = BindCode.objects.get()
        self.assertEqual(bc.code_hash, codes.hash_code(r.code))
        self.assertNotIn(r.code, bc.code_hash)
        self.assertEqual((bc.user, bc.qq, bc.nickname), (self.user, "12345678", "凯拉"))
        self.assertEqual(bc.expires_at, r.expires_at)
        self.assertEqual(bindings.live_code(self.user, now=now), bc)
        self.assertEqual(audits(A.CODE, qq="12345678").count(), 1)
        self.assertFalse(Binding.objects.exists())
        d = el.evaluate(self.group, ["12345678"], now=now)["12345678"]
        self.assertEqual((d.decision, d.reason), (el.DENY, el.PENDING_VERIFY))

    def test_uses_config_ttl(self):
        self.config.code_ttl_minutes = 30
        self.config.save()
        now = timezone.now()
        r = bindings.submit(self.user, "12345678", "凯拉", now=now)
        self.assertEqual(r.expires_at, now + timedelta(minutes=30))

    def test_regenerate_invalidates_old_code(self):
        r1 = bindings.submit(self.user, "12345678", "凯拉")
        r2 = bindings.submit(self.user, "12345678", "凯拉")
        self.assertNotEqual(r1.code, r2.code)
        live = BindCode.objects.filter(invalidated_at__isnull=True)
        self.assertEqual(live.count(), 1)
        self.assertEqual(live.get().code_hash, codes.hash_code(r2.code))
        self.assertEqual(bindings.claim("12345678", r1.code).outcome, "code_used")

    def test_existing_binding_untouched(self):
        old = bind(self.user, "11111111", nickname="旧")
        r = bindings.submit(self.user, "12345678", "新")
        self.assertEqual(r.outcome, "pending")
        b = self.fresh(old.pk)
        self.assertEqual((b.qq, b.nickname, b.status), ("11111111", "旧", "verified"))
        d = el.evaluate(self.group, ["11111111"])["11111111"]
        self.assertEqual(d.decision, el.ALLOW)

    def test_rate_limit(self):
        for i in range(5):
            r = bindings.submit(self.user, "12345678", "凯拉")
            self.assertEqual(r.outcome, "pending", i)
        live_before = bindings.live_code(self.user)
        r = bindings.submit(self.user, "12345678", "凯拉")
        self.assertFalse(r.ok)
        self.assertEqual(r.outcome, "rate_limited")
        self.assertIsNone(r.code)
        # the rejected attempt did not kill the live code
        self.assertEqual(bindings.live_code(self.user), live_before)
        self.assertEqual(BindCode.objects.count(), 5)
        # other users are not affected
        bob = create_member("bob")
        self.assertEqual(bindings.submit(bob, "22345678", "bob").outcome, "pending")
        # the window expires
        cache.clear()
        self.assertEqual(bindings.submit(self.user, "12345678", "凯拉").outcome, "pending")

    def test_rate_limit_does_not_apply_to_trusted(self):
        for _ in range(5):
            bindings.submit(self.user, "12345678", "凯拉")
        put_in_roster(self.group, ["12345678"])
        self.assertEqual(bindings.submit(self.user, "12345678", "凯拉").outcome, "trusted")


class SubmitOtherOutcomesTests(BaseTestCase):
    def test_taken(self):
        bind(create_member("bob"), "12345678")
        put_in_roster(self.group, ["12345678"])
        r = bindings.submit(self.user, "12345678", "凯拉")
        self.assertFalse(r.ok)
        self.assertEqual(r.outcome, "taken")
        self.assertIn("QQ 管理员", r.message)
        self.assertFalse(Binding.objects.filter(user=self.user).exists())
        self.assertFalse(BindCode.objects.exists())

    def test_trusted_by_other_is_not_taken(self):
        bind(create_member("bob"), "12345678", status="trusted")
        r = bindings.submit(self.user, "12345678", "凯拉")
        self.assertEqual(r.outcome, "pending")

    def test_same_qq_updates_nickname(self):
        b = bind(self.user, "12345678", nickname="旧")
        r = bindings.submit(self.user, "12345678", "新")
        self.assertTrue(r.ok)
        self.assertEqual(r.outcome, "nickname_updated")
        self.assertEqual(self.fresh(b.pk).nickname, "新")
        self.assertEqual(self.fresh(b.pk).status, "verified")
        nick = audits(A.NICKNAME).get()
        self.assertEqual((nick.detail["old"], nick.detail["new"]), ("旧", "新"))
        self.assertIn("card", event_kinds("12345678"))
        self.assertFalse(BindCode.objects.exists())

    def test_same_qq_writes_card_event_even_with_override(self):
        b = bind(self.user, "12345678", nickname="旧", card_override="固定")
        bindings.submit(self.user, "12345678", "中")  # fingerprint first computed here
        Event.objects.all().delete()
        bindings.submit(self.user, "12345678", "新")
        self.assertEqual(event_kinds("12345678"), ["card"])
        self.assertEqual(self.fresh(b.pk).nickname, "新")

    def test_same_qq_unchanged(self):
        bind(self.user, "12345678", nickname="同")
        r = bindings.submit(self.user, "12345678", "同")
        self.assertEqual((r.ok, r.outcome), (True, "unchanged"))
        self.assertFalse(AuditLog.objects.exists())

    def test_same_qq_ignores_cooldown(self):
        bind(self.user, "12345678", nickname="旧", qq_changed_at=timezone.now())
        self.assertEqual(bindings.submit(self.user, "12345678", "新").outcome, "nickname_updated")

    def test_cooldown(self):
        t0 = timezone.now()
        b = bind(self.user, "12345678", qq_changed_at=t0)
        now = t0 + timedelta(hours=23)
        r = bindings.submit(self.user, "22345678", "凯拉", now=now)
        self.assertFalse(r.ok)
        self.assertEqual(r.outcome, "cooldown")
        self.assertEqual(r.retry_after, timedelta(hours=1))
        self.assertIn("1 小时", r.message)
        self.assertFalse(BindCode.objects.exists())
        self.assertEqual(self.fresh(b.pk).qq, "12345678")

    def test_cooldown_also_for_trusted(self):
        put_in_roster(self.group, ["22345678"])
        t0 = timezone.now()
        bind(self.user, "12345678", qq_changed_at=t0)
        r = bindings.submit(self.user, "22345678", "凯拉", now=t0 + timedelta(hours=1))
        self.assertEqual(r.outcome, "cooldown")

    def test_cooldown_over(self):
        t0 = timezone.now() - timedelta(hours=24)
        bind(self.user, "12345678", qq_changed_at=t0)
        r = bindings.submit(self.user, "22345678", "凯拉", now=t0 + timedelta(hours=24))
        self.assertEqual(r.outcome, "pending")

    def test_cooldown_config(self):
        t0 = timezone.now()
        bind(self.user, "12345678", qq_changed_at=t0)
        self.config.rebind_cooldown_hours = 0
        self.config.save()
        self.assertEqual(bindings.submit(self.user, "22345678", "凯拉", now=t0).outcome, "pending")
        self.config.rebind_cooldown_hours = 48
        self.config.save()
        r = bindings.submit(self.user, "22345678", "凯拉", now=t0 + timedelta(hours=30))
        self.assertEqual(r.outcome, "cooldown")
        self.assertEqual(r.retry_after, timedelta(hours=18))

    def test_first_binding_has_no_cooldown(self):
        put_in_roster(self.group, ["12345678"])
        self.assertEqual(bindings.submit(self.user, "12345678", "凯拉").outcome, "trusted")

    def test_first_trusted_binding_starts_cooldown(self):
        put_in_roster(self.group, ["12345678", "22345678"])
        now = timezone.now()
        bindings.submit(self.user, "12345678", "凯拉", now=now)
        r = bindings.submit(self.user, "22345678", "凯拉", now=now + timedelta(minutes=5))
        self.assertEqual(r.outcome, "cooldown")

    def test_taken_checked_before_cooldown(self):
        bind(create_member("bob"), "22345678")
        bind(self.user, "12345678", qq_changed_at=timezone.now())
        self.assertEqual(bindings.submit(self.user, "22345678", "凯拉").outcome, "taken")


class CodeHelpersTests(BaseTestCase):
    def test_live_code_and_cancel(self):
        self.assertIsNone(bindings.live_code(self.user))
        now = timezone.now()
        r = bindings.submit(self.user, "12345678", "凯拉", now=now)
        self.assertIsNotNone(bindings.live_code(self.user, now=now))
        self.assertIsNone(bindings.live_code(self.user, now=r.expires_at))
        self.assertEqual(bindings.cancel_code(self.user), 1)
        self.assertIsNone(bindings.live_code(self.user, now=now))
        self.assertEqual(bindings.claim("12345678", r.code).outcome, "code_used")


class ClaimTests(BaseTestCase):
    def pending(self, user=None, qq="12345678", nickname="凯拉", now=None):
        r = bindings.submit(user or self.user, qq, nickname, now=now)
        self.assertEqual(r.outcome, "pending")
        return r

    def test_no_code(self):
        for text in ["", None, "我想加群", "QQ-12"]:
            with self.subTest(text=text):
                r = bindings.claim("12345678", text)
                self.assertEqual((r.ok, r.outcome), (False, "no_code"))

    def test_code_invalid(self):
        self.pending()
        r = bindings.claim("12345678", "QQ-ABCDEF")
        self.assertEqual((r.ok, r.outcome), (False, "code_invalid"))
        self.assertIsNotNone(bindings.live_code(self.user))

    def test_code_expired(self):
        now = timezone.now()
        r = self.pending(now=now)
        c = bindings.claim("12345678", r.code, now=r.expires_at)
        self.assertEqual((c.ok, c.outcome), (False, "code_expired"))
        self.assertFalse(Binding.objects.exists())

    def test_claimed(self):
        now = timezone.now()
        r = self.pending(now=now)
        later = now + timedelta(minutes=1)
        c = bindings.claim("12345678", f"你好，验证码 {r.code.lower().replace('-', '')} 谢谢", now=later)
        self.assertTrue(c.ok)
        self.assertEqual(c.outcome, "claimed")
        self.assertEqual(c.qq, "12345678")
        b = Binding.objects.get(user=self.user)
        self.assertEqual(c.binding.pk, b.pk)
        self.assertEqual((b.qq, b.nickname, b.status, b.verified_via), ("12345678", "凯拉", "verified", "code"))
        self.assertEqual(b.verified_at, later)
        self.assertEqual(b.qq_changed_at, later)
        self.assertEqual(BindCode.objects.get().used_at, later)
        verify = audits(A.VERIFY).get()
        self.assertEqual((verify.qq, verify.target_name, verify.actor_name), ("12345678", "alice", ""))
        self.assertEqual(event_kinds("12345678"), ["recheck", "card"])
        d = el.evaluate(self.group, ["12345678"], now=later)["12345678"]
        self.assertEqual((d.decision, d.card), (el.ALLOW, "[IGC] Kaela Voss - 凯拉"))

    def test_double_spend(self):
        r = self.pending()
        self.assertEqual(bindings.claim("12345678", r.code).outcome, "claimed")
        second = bindings.claim("12345678", r.code)
        self.assertEqual((second.ok, second.outcome), (False, "code_used"))
        self.assertEqual(audits(A.VERIFY).count(), 1)

    def test_conditional_update_protects_against_race(self):
        """Simulate a concurrent claim that used the code after we read it."""
        r = self.pending()
        stale = BindCode.objects.get()
        BindCode.objects.filter(pk=stale.pk).update(used_at=timezone.now())
        original_first = QuerySet.first

        def fake_first(qs):
            if qs.model is BindCode:
                return stale
            return original_first(qs)

        with mock.patch.object(QuerySet, "first", fake_first):
            c = bindings.claim("12345678", r.code)
        self.assertEqual((c.ok, c.outcome), (False, "code_used"))
        self.assertFalse(Binding.objects.exists())
        self.assertFalse(audits(A.VERIFY).exists())

    def test_qq_mismatch_invalidates_code(self):
        r = self.pending()
        c = bindings.claim("99999999", r.code)
        self.assertEqual((c.ok, c.outcome), (False, "qq_mismatch"))
        self.assertIsNotNone(BindCode.objects.get().invalidated_at)
        failed = audits(A.CLAIM_FAILED).get()
        self.assertEqual(failed.qq, "12345678")
        self.assertEqual(failed.detail["applicant_qq"], "99999999")
        self.assertEqual(failed.target_name, "alice")
        # the real owner cannot use it any more either
        self.assertEqual(bindings.claim("12345678", r.code).outcome, "code_used")
        self.assertFalse(Binding.objects.exists())

    def test_invalid_applicant_qq_is_mismatch(self):
        r = self.pending()
        self.assertEqual(bindings.claim("bad", r.code).outcome, "qq_mismatch")

    def test_cancelled_code(self):
        r = self.pending()
        bindings.cancel_code(self.user)
        self.assertEqual(bindings.claim("12345678", r.code).outcome, "code_used")

    def test_claim_rebind(self):
        old = bind(self.user, "11111111", nickname="旧", status="trusted", card_override="X",
                   qq_changed_at=timezone.now() - timedelta(days=5))
        r = self.pending(nickname="新")
        # untouched until claimed
        self.assertEqual(self.fresh(old.pk).qq, "11111111")
        now = timezone.now()
        c = bindings.claim("12345678", r.code, now=now)
        self.assertEqual(c.outcome, "claimed")
        b = self.fresh(old.pk)
        self.assertEqual((b.qq, b.nickname, b.status), ("12345678", "新", "verified"))
        self.assertEqual(b.qq_changed_at, now)
        self.assertEqual(b.card_override, "")
        self.assertIn("recheck", event_kinds("11111111"))
        self.assertEqual(audits(A.VERIFY).get().detail["old_qq"], "11111111")
        self.assertEqual(Binding.objects.count(), 1)

    def test_claim_same_qq_upgrades_trusted_keeps_changed_at(self):
        t0 = timezone.now() - timedelta(days=5)
        b = bind(self.user, "12345678", status="trusted", qq_changed_at=t0)
        bc = BindCode.objects.create(
            user=self.user, qq="12345678", nickname="新", code_hash=codes.hash_code("QQ-ABCDEF"),
            expires_at=timezone.now() + timedelta(minutes=10),
        )
        c = bindings.claim("12345678", "QQ-ABCDEF")
        self.assertEqual(c.outcome, "claimed")
        b = self.fresh(b.pk)
        self.assertEqual((b.status, b.nickname, b.qq_changed_at), ("verified", "新", t0))
        self.assertIsNotNone(BindCode.objects.get(pk=bc.pk).used_at)

    def test_takeover_of_verified_binding(self):
        bob = create_member("bob")
        bob_binding = bind(bob, "12345678", status="verified")
        # alice can't submit a verified QQ; the code must have been issued
        # before bob verified it.
        BindCode.objects.create(
            user=self.user, qq="12345678", nickname="凯拉", code_hash=codes.hash_code("QQ-ABCDEF"),
            expires_at=timezone.now() + timedelta(minutes=10),
        )
        c = bindings.claim("12345678", "QQ-ABCDEF")
        self.assertEqual(c.outcome, "claimed")
        self.assertFalse(Binding.objects.filter(pk=bob_binding.pk).exists())
        self.assertEqual(Binding.objects.get(qq="12345678").user, self.user)
        resolved = audits(A.CONFLICT_RESOLVED).get()
        self.assertEqual(resolved.target_name, "bob")
        self.assertEqual(resolved.detail["reason"], "takeover")
        self.assertEqual(resolved.detail["previous_status"], "verified")
        self.assertEqual(resolved.detail["winner"], "alice")

    def test_resolves_trusted_conflict(self):
        bob = create_member("bob")
        carol = create_member("carol")
        bind(bob, "12345678", status="trusted")
        bind(carol, "12345678", status="trusted")
        r = self.pending()
        c = bindings.claim("12345678", r.code)
        self.assertEqual(c.outcome, "claimed")
        self.assertEqual(list(Binding.objects.filter(qq="12345678").values_list("user__username", flat=True)),
                         ["alice"])
        resolved = audits(A.CONFLICT_RESOLVED)
        self.assertEqual(set(resolved.values_list("target_name", flat=True)), {"bob", "carol"})
        self.assertEqual({a.detail["reason"] for a in resolved}, {"conflict"})
        self.assertEqual(bindings.conflicts(), [])
        d = el.evaluate(self.group, ["12345678"])["12345678"]
        self.assertEqual(d.decision, el.ALLOW)

    def test_own_trusted_conflict_resolved_by_claim(self):
        bob = create_member("bob")
        put_in_roster(self.group, ["12345678"])
        bindings.submit(bob, "12345678", "bob")
        bindings.submit(self.user, "12345678", "凯拉")
        self.assertEqual(len(bindings.conflicts()), 1)
        BindCode.objects.create(
            user=self.user, qq="12345678", nickname="凯拉", code_hash=codes.hash_code("QQ-ABCDEF"),
            expires_at=timezone.now() + timedelta(minutes=10),
        )
        self.assertEqual(bindings.claim("12345678", "qq-abcdef").outcome, "claimed")
        self.assertEqual(Binding.objects.get(qq="12345678").status, "verified")
        self.assertFalse(Binding.objects.filter(user=bob).exists())


class ConfirmTests(BaseTestCase):
    def setUp(self):
        super().setUp()
        self.manager = create_member("boss")

    def test_confirm(self):
        b = bind(self.user, "12345678", status="trusted")
        bob = create_member("bob")
        bind(bob, "12345678", status="trusted")
        r = bindings.confirm(b, self.manager)
        self.assertTrue(r.ok)
        self.assertEqual(r.outcome, "confirmed")
        b = self.fresh(b.pk)
        self.assertEqual((b.status, b.verified_via), ("verified", "manager"))
        self.assertIsNotNone(b.verified_at)
        self.assertFalse(Binding.objects.filter(user=bob).exists())
        resolved = audits(A.CONFLICT_RESOLVED).get()
        self.assertEqual((resolved.actor_name, resolved.target_name), ("boss", "bob"))
        confirm = audits(A.CONFIRM).get()
        self.assertEqual((confirm.actor_name, confirm.target_name), ("boss", "alice"))
        self.assertIn("recheck", event_kinds("12345678"))

    def test_refused_when_other_verified(self):
        b = bind(self.user, "12345678", status="trusted")
        bind(create_member("bob"), "12345678", status="verified")
        r = bindings.confirm(b, self.manager)
        self.assertEqual((r.ok, r.outcome), (False, "taken"))
        self.assertEqual(self.fresh(b.pk).status, "trusted")

    def test_already_verified(self):
        b = bind(self.user, "12345678", status="verified")
        r = bindings.confirm(b, self.manager)
        self.assertEqual((r.ok, r.outcome), (True, "unchanged"))
        self.assertEqual(self.fresh(b.pk).verified_via, "code")


class UnbindTests(BaseTestCase):
    def test_unbind(self):
        bind(self.user, "12345678")
        bindings.submit(self.user, "22345678", "凯拉")
        r = bindings.unbind(self.user, actor=self.user)
        self.assertTrue(r.ok)
        self.assertEqual(r.outcome, "unbound")
        self.assertFalse(Binding.objects.exists())
        self.assertIsNone(bindings.live_code(self.user))
        self.assertIn("recheck", event_kinds("12345678"))
        u = audits(A.UNBIND).get()
        self.assertEqual((u.qq, u.actor_name, u.target_name), ("12345678", "alice", "alice"))

    def test_forced(self):
        manager = create_member("boss")
        bind(self.user, "12345678")
        bindings.unbind(self.user, actor=manager, forced=True)
        u = audits(A.FORCE_UNBIND).get()
        self.assertEqual((u.actor_name, u.target_name), ("boss", "alice"))
        self.assertFalse(audits(A.UNBIND).exists())

    def test_not_bound(self):
        r = bindings.submit(self.user, "22345678", "凯拉")
        u = bindings.unbind(self.user)
        self.assertEqual((u.ok, u.outcome), (False, "not_bound"))
        self.assertEqual(bindings.claim("22345678", r.code).outcome, "code_used")

    def test_unbind_resolves_conflict_for_other(self):
        bind(self.user, "12345678", status="trusted")
        bind(create_member("bob"), "12345678", status="trusted")
        bindings.unbind(self.user)
        d = el.evaluate(self.group, ["12345678"])["12345678"]
        self.assertEqual(d.decision, el.ALLOW)


class NicknameAndCardTests(BaseTestCase):
    def test_set_nickname(self):
        b = bind(self.user, "12345678", nickname="旧")
        r = bindings.set_nickname(self.user, "新")
        self.assertEqual((r.ok, r.outcome), (True, "nickname_updated"))
        self.assertEqual(self.fresh(b.pk).nickname, "新")
        self.assertTrue(audits(A.NICKNAME).exists())
        self.assertIn("card", event_kinds("12345678"))
        self.assertEqual(bindings.set_nickname(self.user, "新").outcome, "unchanged")

    def test_set_nickname_invalid_or_unbound(self):
        self.assertEqual(bindings.set_nickname(self.user, "新").outcome, "not_bound")
        bind(self.user, "12345678")
        self.assertEqual(bindings.set_nickname(self.user, "a  b").outcome, "invalid")

    def test_set_card_override(self):
        manager = create_member("boss")
        b = bind(self.user, "12345678")
        bindings.set_card_override(b, "x", manager)  # establish fingerprint
        Event.objects.all().delete()
        r = bindings.set_card_override(b, "  指定  名片 ", manager)
        self.assertEqual((r.ok, r.outcome), (True, "card_set"))
        b = self.fresh(b.pk)
        self.assertEqual(b.card_override, "指定 名片")
        self.assertEqual(event_kinds("12345678"), ["card"])
        card_audit = audits(A.CARD).order_by("id").last()
        self.assertEqual((card_audit.actor_name, card_audit.detail["new"]), ("boss", "指定 名片"))
        d = el.evaluate(self.group, ["12345678"])["12345678"]
        self.assertEqual(d.card, "指定 名片")
        r = bindings.set_card_override(b, "", manager)
        self.assertEqual(r.outcome, "card_cleared")
        self.assertEqual(self.fresh(b.pk).card_override, "")
        self.assertEqual(el.evaluate(self.group, ["12345678"])["12345678"].card, "[IGC] Kaela Voss - n")

    def test_set_card_override_too_long(self):
        b = bind(self.user, "12345678")
        self.assertEqual(bindings.set_card_override(b, "中" * 20, None).outcome, "card_set")
        r = bindings.set_card_override(b, "中" * 21, None)  # 63 bytes
        self.assertEqual((r.ok, r.outcome), (False, "invalid"))
        self.assertEqual(self.fresh(b.pk).card_override, "中" * 20)


class ConflictsTests(BaseTestCase):
    def test_conflicts(self):
        bind(self.user, "12345678", status="trusted")
        bind(create_member("b"), "12345678", status="trusted")
        bind(create_member("c"), "22345678", status="trusted")
        bind(create_member("d"), "32345678", status="trusted")
        bind(create_member("e"), "32345678", status="trusted")
        bind(create_member("f"), "32345678", status="verified")
        bind(create_member("g"), "42345678", status="verified")
        result = bindings.conflicts()
        self.assertEqual([qq for qq, _ in result], ["12345678"])
        self.assertEqual({b.user.username for b in result[0][1]}, {"alice", "b"})


class UserDeletedTests(BaseTestCase):
    def test_on_user_deleted(self):
        bind(self.user, "12345678")
        bindings.on_user_deleted(self.user)
        self.assertIn("recheck", event_kinds("12345678"))
        entry = audits(A.USER_DELETED).get()
        self.assertEqual((entry.qq, entry.target_name), ("12345678", "alice"))
        self.user.delete()
        entry.refresh_from_db()
        self.assertIsNone(entry.target_user)
        self.assertEqual(entry.target_name, "alice")
        self.assertFalse(Binding.objects.exists())

    def test_on_user_deleted_without_binding(self):
        bindings.on_user_deleted(self.user)
        self.assertFalse(AuditLog.objects.exists())
        self.assertFalse(Event.objects.exists())


class LockOrderTests(BaseTestCase):
    """Every write takes the user lock, then the QQ locks (target and old
    QQ), and only then row locks: one global order, so concurrent writes
    serialize instead of deadlocking (docs in core/locks.py). SQLite ignores
    row locks, so the order itself is checked here; see
    test_core_concurrency.py for real databases."""

    def setUp(self):
        super().setUp()
        self.calls = []
        real_user, real_qqs = locks.lock_user, locks.lock_qqs
        real_binding, real_invalidate = bindings._user_binding_for_update, bindings._invalidate_codes

        def lock_user(user_id):
            self.calls.append(("user", user_id))
            return real_user(user_id)

        def lock_qqs(*qqs):
            self.calls.append(("qqs", {q for q in qqs if q}))
            return real_qqs(*qqs)

        def binding_for_update(user):
            self.calls.append(("binding",))
            return real_binding(user)

        def invalidate(user, now):
            self.calls.append(("codes",))
            return real_invalidate(user, now)

        for target, attr, fn in [(locks, "lock_user", lock_user), (locks, "lock_qqs", lock_qqs),
                                 (bindings, "_user_binding_for_update", binding_for_update),
                                 (bindings, "_invalidate_codes", invalidate)]:
            patcher = mock.patch.object(target, attr, fn)
            patcher.start()
            self.addCleanup(patcher.stop)

    def kinds(self):
        return [c[0] for c in self.calls]

    def assertLockOrder(self, user, qqs=None):
        kinds = self.kinds()
        self.assertEqual(self.calls[0], ("user", user.pk))
        self.assertEqual(kinds.count("user"), 1)
        if qqs is not None:
            self.assertEqual(kinds.count("qqs"), 1)
            self.assertEqual(self.calls[kinds.index("qqs")][1], set(qqs))
            first_row = min((kinds.index(k) for k in ("binding", "codes") if k in kinds),
                            default=len(kinds))
            self.assertLess(kinds.index("qqs"), first_row)

    def test_submit_pending_with_binding(self):
        bind(self.user, "11111111")
        self.assertEqual(bindings.submit(self.user, "12345678", "凯拉").outcome, "pending")
        self.assertLockOrder(self.user, {"12345678", "11111111"})

    def test_submit_trusted_without_binding(self):
        put_in_roster(self.group, ["12345678"])
        self.assertEqual(bindings.submit(self.user, "12345678", "凯拉").outcome, "trusted")
        self.assertLockOrder(self.user, {"12345678"})

    def test_claim(self):
        bind(self.user, "11111111", qq_changed_at=timezone.now() - timedelta(days=2))
        r = bindings.submit(self.user, "12345678", "凯拉")
        self.calls.clear()
        self.assertEqual(bindings.claim("12345678", r.code).outcome, "claimed")
        self.assertLockOrder(self.user, {"12345678", "11111111"})

    def test_claim_mismatch_takes_no_qq_lock(self):
        r = bindings.submit(self.user, "12345678", "凯拉")
        self.calls.clear()
        self.assertEqual(bindings.claim("22345678", r.code).outcome, "qq_mismatch")
        self.assertEqual(self.kinds(), ["user"])

    def test_unbind_cancel_nickname(self):
        bind(self.user, "12345678")
        bindings.set_nickname(self.user, "新")
        self.assertLockOrder(self.user)
        self.calls.clear()
        bindings.cancel_code(self.user)
        self.assertLockOrder(self.user)
        self.calls.clear()
        bindings.unbind(self.user)
        self.assertLockOrder(self.user, {"12345678"})

    def test_confirm_and_card_lock_the_binding_owner(self):
        b = bind(self.user, "12345678", status="trusted")
        manager = create_member("boss")
        bindings.set_card_override(b, "x", manager)
        self.assertLockOrder(self.user)
        self.calls.clear()
        bindings.confirm(b, manager)
        self.assertLockOrder(self.user, {"12345678"})

    def test_on_user_deleted(self):
        bind(self.user, "12345678")
        bindings.on_user_deleted(self.user)
        self.assertLockOrder(self.user, {"12345678"})


class LockRowsTests(BaseTestCase):
    def test_rows_created_by_migration(self):
        self.assertEqual(sorted(Lock.objects.values_list("pk", flat=True)), locks.all_keys())

    def test_keys_are_ordered_users_before_qqs(self):
        self.assertLess(max(locks.user_key(pk) for pk in range(500)),
                        min(locks.qq_key(qq) for qq in ["10000", "12345678", "99999999999"]))

    def test_missing_rows_are_recreated(self):
        Lock.objects.all().delete()
        put_in_roster(self.group, ["12345678"])
        self.assertEqual(bindings.submit(self.user, "12345678", "凯拉").outcome, "trusted")
        self.assertEqual(Lock.objects.count(), 2)
        locks.ensure_rows()
        self.assertEqual(Lock.objects.count(), len(locks.all_keys()))


class VerifiedQQUniqueTests(BaseTestCase):
    """The "one verified owner per QQ" guarantee must not depend on partial
    indexes, which MySQL/MariaDB do not have."""

    def test_no_conditional_unique_constraint(self):
        for constraint in Binding._meta.constraints:
            if isinstance(constraint, UniqueConstraint):
                self.assertIsNone(constraint.condition, constraint.name)
        self.assertTrue(Binding._meta.get_field("verified_qq").unique)

    def test_duplicate_verified_rejected(self):
        bind(self.user, "12345678")
        with self.assertRaises(IntegrityError), transaction.atomic():
            bind(create_member("bob"), "12345678")
        bind(create_member("carol"), "12345678", status="trusted")  # trusted may repeat

    def test_verified_qq_follows_status_and_qq(self):
        b = bind(self.user, "12345678", status="trusted")
        self.assertIsNone(self.fresh(b.pk).verified_qq)
        b.status = "verified"
        b.save(update_fields=["status"])
        self.assertEqual(self.fresh(b.pk).verified_qq, "12345678")
        b.qq = "22345678"
        b.save(update_fields=["qq"])
        self.assertEqual(self.fresh(b.pk).verified_qq, "22345678")
        b.status = "trusted"
        b.save()
        self.assertIsNone(self.fresh(b.pk).verified_qq)

    def test_raw_update_cannot_desync(self):
        b = bind(self.user, "12345678")
        with self.assertRaises(IntegrityError), transaction.atomic():
            Binding.objects.filter(pk=b.pk).update(status="trusted")

    def test_claim_retries_once_on_integrity_error(self):
        r = bindings.submit(self.user, "12345678", "凯拉")
        real_save = Binding.save
        calls = []

        def flaky_save(binding, *args, **kwargs):
            calls.append(binding.status)
            if len(calls) == 1:
                raise IntegrityError("simulated concurrent verify")
            return real_save(binding, *args, **kwargs)

        with mock.patch.object(Binding, "save", flaky_save), \
                self.assertLogs(bindings.logger, "WARNING"):
            c = bindings.claim("12345678", r.code)
        self.assertEqual(c.outcome, "claimed")
        self.assertEqual(len(calls), 2)
        self.assertEqual(audits(A.VERIFY).count(), 1)
        self.assertEqual(Binding.objects.get().status, "verified")
