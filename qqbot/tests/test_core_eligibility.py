from datetime import timedelta

from django.contrib.auth.models import Group
from django.core.cache import cache
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from allianceauth.authentication.models import UserProfile
from allianceauth.tests.auth_utils import AuthUtils

from ..core import bindings, codes
from ..core import eligibility as el
from ..models import Binding, BindCode, Config
from .utils import (
    add_to_groups,
    basic_access_permission,
    bind,
    create_group,
    create_member,
    create_user,
    member_state,
    no_access_state,
)


def one(group, qq):
    return el.evaluate(group, [qq])[qq]


class EvaluateReasonTests(TestCase):
    def setUp(self):
        self.fixed = create_group(100001, name="聊天群")

    def assertDecision(self, d, decision, reason):
        self.assertEqual((d.decision, d.reason), (decision, reason))

    def test_constants(self):
        self.assertEqual((el.ALLOW, el.DENY, el.REVIEW), ("allow", "deny", "review"))
        for name in ["OK", "NOT_BOUND", "PENDING_VERIFY", "CONFLICT", "USER_INACTIVE",
                     "NO_MAIN", "NO_ACCESS", "GROUP_ROLE_MISSING", "GROUP_MISCONFIGURED"]:
            self.assertEqual(getattr(el, name), name)

    def test_allow_with_card(self):
        user = create_member("alice", character_name="Kaela Voss")
        bind(user, "12345678", nickname="凯拉")
        d = one(self.fixed, "12345678")
        self.assertDecision(d, el.ALLOW, el.OK)
        self.assertEqual(d.card, "[IGC] Kaela Voss - 凯拉")
        self.assertEqual(d.qq, "12345678")

    def test_not_bound(self):
        d = one(self.fixed, "12345678")
        self.assertDecision(d, el.DENY, el.NOT_BOUND)
        self.assertIsNone(d.card)

    def _code(self, user, qq, **kw):
        kw.setdefault("expires_at", timezone.now() + timedelta(minutes=10))
        return BindCode.objects.create(
            user=user, qq=qq, nickname="n", code_hash=codes.hash_code(codes.generate_code()), **kw
        )

    def test_pending_verify(self):
        user = create_member("alice")
        self._code(user, "12345678")
        self.assertDecision(one(self.fixed, "12345678"), el.DENY, el.PENDING_VERIFY)

    def test_expired_used_or_invalidated_code_is_not_pending(self):
        user = create_member("alice")
        now = timezone.now()
        self._code(user, "11111111", expires_at=now - timedelta(minutes=1))
        self._code(user, "22222222", used_at=now)
        self._code(user, "33333333", invalidated_at=now)
        for qq in ["11111111", "22222222", "33333333"]:
            with self.subTest(qq=qq):
                self.assertDecision(one(self.fixed, qq), el.DENY, el.NOT_BOUND)

    def test_pending_code_ignored_when_bound(self):
        user = create_member("alice")
        other = create_member("bob")
        bind(user, "12345678")
        self._code(other, "12345678")
        self.assertDecision(one(self.fixed, "12345678"), el.ALLOW, el.OK)

    def test_conflict(self):
        bind(create_member("a"), "12345678", status="trusted")
        bind(create_member("b"), "12345678", status="trusted")
        d = one(self.fixed, "12345678")
        self.assertDecision(d, el.REVIEW, el.CONFLICT)
        self.assertIsNone(d.card)

    def test_conflict_even_if_users_ineligible(self):
        bind(create_member("a", active=False), "12345678", status="trusted")
        bind(create_member("b", state_perm=False), "12345678", status="trusted")
        self.assertDecision(one(self.fixed, "12345678"), el.REVIEW, el.CONFLICT)

    def test_single_trusted_allows(self):
        bind(create_member("a"), "12345678", status="trusted")
        self.assertDecision(one(self.fixed, "12345678"), el.ALLOW, el.OK)

    def test_verified_overrides_trusted(self):
        winner = create_member("winner", character_name="Win")
        bind(create_member("t1"), "12345678", status="trusted")
        bind(winner, "12345678", status="verified", nickname="w")
        bind(create_member("t2"), "12345678", status="trusted")
        d = one(self.fixed, "12345678")
        self.assertDecision(d, el.ALLOW, el.OK)
        self.assertEqual(d.card, "[IGC] Win - w")

    def test_verified_owner_decides_even_when_denied(self):
        bind(create_member("t1"), "12345678", status="trusted")
        bind(create_member("v", active=False), "12345678", status="verified")
        self.assertDecision(one(self.fixed, "12345678"), el.DENY, el.USER_INACTIVE)

    def test_user_inactive(self):
        bind(create_member("a", active=False), "12345678")
        self.assertDecision(one(self.fixed, "12345678"), el.DENY, el.USER_INACTIVE)

    def test_inactive_checked_before_other_rules(self):
        bind(create_user("a", main=False, state=no_access_state(), active=False), "12345678")
        self.assertDecision(one(self.fixed, "12345678"), el.DENY, el.USER_INACTIVE)

    def test_no_main(self):
        bind(create_user("a", main=False, state=member_state()), "12345678")
        self.assertDecision(one(self.fixed, "12345678"), el.DENY, el.NO_MAIN)

    def test_no_main_checked_before_access(self):
        bind(create_user("a", main=False, state=no_access_state()), "12345678")
        self.assertDecision(one(self.fixed, "12345678"), el.DENY, el.NO_MAIN)

    def test_no_access(self):
        bind(create_member("a", state_perm=False), "12345678")
        self.assertDecision(one(self.fixed, "12345678"), el.DENY, el.NO_ACCESS)

    def test_superuser_without_permission_has_no_access(self):
        bind(create_user("root", state=no_access_state(), superuser=True), "12345678")
        self.assertDecision(one(self.fixed, "12345678"), el.DENY, el.NO_ACCESS)

    def test_access_via_group(self):
        user = create_member("a", state_perm=False)
        group = Group.objects.create(name="QQ")
        group.permissions.add(basic_access_permission())
        add_to_groups(user, group)
        bind(user, "12345678")
        self.assertDecision(one(self.fixed, "12345678"), el.ALLOW, el.OK)

    def test_access_via_user_permission(self):
        user = create_member("a", state_perm=False)
        AuthUtils.add_permissions_to_user([basic_access_permission()], user)
        bind(user, "12345678")
        self.assertDecision(one(self.fixed, "12345678"), el.ALLOW, el.OK)

    def test_role_group_member(self):
        cap = Group.objects.create(name="Cap")
        role = create_group(200001, kind="role", required=[cap, "FC"])
        user = create_member("a")
        add_to_groups(user, cap)
        bind(user, "12345678")
        self.assertDecision(one(role, "12345678"), el.ALLOW, el.OK)

    def test_role_group_any_of_required(self):
        role = create_group(200001, kind="role", required=["Cap", "FC"])
        user = create_member("a")
        add_to_groups(user, "FC")
        bind(user, "12345678")
        self.assertDecision(one(role, "12345678"), el.ALLOW, el.OK)

    def test_role_group_missing(self):
        role = create_group(200001, kind="role", required=["Cap"])
        user = create_member("a")
        add_to_groups(user, "Other")
        bind(user, "12345678")
        d = one(role, "12345678")
        self.assertDecision(d, el.DENY, el.GROUP_ROLE_MISSING)
        self.assertIsNone(d.card)

    def test_role_group_misconfigured(self):
        role = create_group(200001, kind="role")
        bind(create_member("a"), "12345678")
        self.assertDecision(one(role, "12345678"), el.REVIEW, el.GROUP_MISCONFIGURED)

    def test_no_access_checked_before_group_rules(self):
        role = create_group(200001, kind="role")
        bind(create_member("a", state_perm=False), "12345678")
        self.assertDecision(one(role, "12345678"), el.DENY, el.NO_ACCESS)

    def test_fixed_group_ignores_required_groups(self):
        fixed = create_group(300001, kind="fixed", required=["Cap"])
        bind(create_member("a"), "12345678")
        self.assertDecision(one(fixed, "12345678"), el.ALLOW, el.OK)

    def test_many_qqs_and_normalization(self):
        bind(create_member("a"), "12345678")
        result = el.evaluate(self.fixed, ["12345678", "１２３４５６７８", " 87654321 ", "bad", "0123", 23456789])
        self.assertEqual(set(result), {"12345678", "87654321", "23456789"})
        self.assertEqual(result["12345678"].reason, el.OK)
        self.assertEqual(result["87654321"].reason, el.NOT_BOUND)
        self.assertEqual(el.evaluate(self.fixed, []), {})

    def test_decision_is_frozen(self):
        d = one(self.fixed, "12345678")
        with self.assertRaises(Exception):
            d.reason = "x"


# Rows that exercise every branch of the rules; placed both inside the first
# 10 QQs and further on, so the small and the large query count cover them.
SPECIAL_ROWS = ["conflict", "shadowed", "inactive", "nomain", "override", "noprofile"]
SPECIAL_AT = {base + k: kind for base in (1, 150, 250) for k, kind in enumerate(SPECIAL_ROWS)}


def _special_row(i, qq, cap):
    kind = SPECIAL_AT[i]
    if kind == "conflict":
        bind(create_member(f"c1_{i}"), qq, status="trusted")
        bind(create_member(f"c2_{i}"), qq, status="trusted")
    elif kind == "shadowed":
        bind(create_member(f"t_{i}"), qq, status="trusted")
        owner = create_member(f"v_{i}")
        add_to_groups(owner, cap)
        bind(owner, qq, status="verified")
    elif kind == "inactive":
        bind(create_member(f"in_{i}", active=False), qq)
    elif kind == "nomain":
        bind(create_user(f"nm_{i}", main=False, state=member_state()), qq)
    elif kind == "override":
        user = create_member(f"ov_{i}")
        add_to_groups(user, cap)
        bind(user, qq, card_override=f"指定{i}")
    elif kind == "noprofile":
        user = create_member(f"np_{i}")
        AuthUtils.disconnect_signals()
        try:
            UserProfile.objects.filter(user=user).delete()
        finally:
            AuthUtils.connect_signals()
        bind(user, qq)


class EvaluateQueryCountTests(TestCase):
    """evaluate() uses a constant number of queries, independent of the
    number of QQs."""

    @classmethod
    def setUpTestData(cls):
        cls.cap = Group.objects.create(name="Cap")
        cls.fixed = create_group(100001)
        cls.role = create_group(200001, kind="role", required=[cls.cap])
        cls.qqs = []
        now = timezone.now()
        for i in range(300):
            qq = str(10_000_000 + i)
            cls.qqs.append(qq)
            if i in SPECIAL_AT:
                _special_row(i, qq, cls.cap)
                continue
            if i % 3 == 0:
                continue  # unbound
            user = create_member(f"user{i}", state_perm=(i % 5 != 0))
            if i % 2:
                add_to_groups(user, cls.cap)
            if i % 7 == 0:
                BindCode.objects.create(
                    user=user, qq=qq, nickname="n",
                    code_hash=codes.hash_code(codes.generate_code()),
                    expires_at=now + timedelta(minutes=10),
                )
                continue  # pending
            bind(user, qq, status="trusted" if i % 4 == 0 else "verified")
        Config.get_solo()

    def count(self, group, qqs):
        with CaptureQueriesContext(connection) as ctx:
            result = el.evaluate(group, qqs)
        self.assertEqual(len(result), len(qqs))
        return len(ctx.captured_queries)

    def test_fixed_group(self):
        small = self.count(self.fixed, self.qqs[:10])
        large = self.count(self.fixed, self.qqs)
        self.assertEqual(small, large)
        with self.assertNumQueries(small):
            el.evaluate(self.fixed, self.qqs)

    def test_role_group(self):
        small = self.count(self.role, self.qqs[:10])
        large = self.count(self.role, self.qqs)
        self.assertEqual(small, large)

    def test_result_mix(self):
        result = el.evaluate(self.role, self.qqs)
        reasons = {d.reason for d in result.values()}
        self.assertTrue({el.OK, el.NOT_BOUND, el.PENDING_VERIFY, el.NO_ACCESS,
                         el.GROUP_ROLE_MISSING, el.CONFLICT, el.USER_INACTIVE,
                         el.NO_MAIN} <= reasons)

    def test_small_slice_covers_every_branch(self):
        """The 10-QQ side of the comparison reaches the special branches too,
        so a per-QQ query on one of them changes the small count."""
        small = el.evaluate(self.role, self.qqs[:10])
        reasons = {d.reason for d in small.values()}
        self.assertTrue({el.OK, el.NOT_BOUND, el.CONFLICT, el.USER_INACTIVE, el.NO_MAIN}
                        <= reasons)
        by_kind = {SPECIAL_AT[i]: small[self.qqs[i]] for i in range(10) if i in SPECIAL_AT}
        self.assertEqual(by_kind["override"].card, "指定5")
        self.assertEqual(by_kind["shadowed"].decision, el.ALLOW)
        self.assertEqual(by_kind["noprofile"].reason, el.NO_MAIN)

    def test_evaluate_many_matches_evaluate_binding(self):
        groups = [self.fixed, self.role]
        many = el.evaluate_many(groups, self.qqs)
        for b in Binding.objects.all():
            with self.subTest(qq=b.qq):
                self.assertEqual(many[b.qq], el.evaluate_binding(b, groups))
        small = self.count_many(groups, self.qqs[:10])
        large = self.count_many(groups, self.qqs)
        self.assertEqual(small, large)

    def count_many(self, groups, qqs):
        with CaptureQueriesContext(connection) as ctx:
            el.evaluate_many(groups, qqs)
        return len(ctx.captured_queries)

    def test_more_than_one_chunk(self):
        many = self.qqs + [str(20_000_000 + i) for i in range(400)]
        self.assertEqual(len(el.evaluate(self.fixed, many)), 700)


class EvaluateBindingTests(TestCase):
    def setUp(self):
        cache.clear()  # code rate limit
        self.fixed = create_group(100001)
        self.role = create_group(200001, kind="role", required=["Cap"])
        self.inactive = create_group(300001, is_active=False)
        self.user = create_member("alice")
        self.binding = bind(self.user, "12345678")

    def test_keyed_by_group_pk_active_only(self):
        result = el.evaluate_binding(self.binding)
        self.assertEqual(set(result), {self.fixed.pk, self.role.pk})
        self.assertEqual(result[self.fixed.pk].decision, el.ALLOW)
        self.assertEqual(result[self.role.pk].reason, el.GROUP_ROLE_MISSING)

    def test_explicit_groups(self):
        result = el.evaluate_binding(self.binding, groups=[self.inactive])
        self.assertEqual(set(result), {self.inactive.pk})
        self.assertEqual(el.evaluate_binding(self.binding, groups=[]), {})

    def test_groups_for_user(self):
        self.assertEqual(el.groups_for_user(self.user), [self.fixed])
        add_to_groups(self.user, "Cap")
        self.assertEqual(el.groups_for_user(self.user), [self.fixed, self.role])

    def test_groups_for_user_unbound_without_code(self):
        self.assertEqual(el.groups_for_user(create_member("bob")), [])

    def test_groups_for_user_pending(self):
        """A new member with a live code gets the groups to apply to."""
        bob = create_member("bob")
        add_to_groups(bob, "Cap")
        r = bindings.submit(bob, "87654321", "bob")
        self.assertEqual(r.outcome, "pending")
        self.assertEqual(el.groups_for_user(bob), [self.fixed, self.role])
        carol = create_member("carol")
        self.assertEqual(bindings.submit(carol, "97654321", "c").outcome, "pending")
        self.assertEqual(el.groups_for_user(carol), [self.fixed])

    def test_groups_for_user_pending_not_eligible(self):
        bob = create_member("bob", state_perm=False)
        self.assertEqual(bindings.submit(bob, "87654321", "bob").outcome, "pending")
        self.assertEqual(el.groups_for_user(bob), [])
        dave = create_member("dave", active=False)
        self.assertEqual(bindings.submit(dave, "97654321", "d").outcome, "pending")
        self.assertEqual(el.groups_for_user(dave), [])

    def test_groups_for_user_expired_or_cancelled_code(self):
        bob = create_member("bob")
        r = bindings.submit(bob, "87654321", "bob")
        self.assertEqual(el.groups_for_user(bob, now=r.expires_at), [])
        bindings.cancel_code(bob)
        self.assertEqual(el.groups_for_user(bob), [])

    def test_groups_for_user_pending_while_in_conflict(self):
        """Rebinding by code out of a conflict: the code decides, so the
        groups to apply to are listed."""
        self.binding.status = "trusted"
        self.binding.save()
        bind(create_member("bob"), "12345678", status="trusted")
        self.assertEqual(el.groups_for_user(self.user), [])
        self.assertEqual(bindings.submit(self.user, "87654321", "n").outcome, "pending")
        self.assertEqual(el.groups_for_user(self.user), [self.fixed])

    def test_groups_for_user_conflict(self):
        self.binding.status = "trusted"
        self.binding.save()
        bind(create_member("bob"), "12345678", status="trusted")
        self.assertEqual(el.groups_for_user(self.user), [])

    def test_groups_for_user_overshadowed_trusted(self):
        bob = create_member("bob")
        bind(bob, "87654321", status="trusted")
        bind(create_member("carol"), "87654321", status="verified")
        self.assertEqual(el.groups_for_user(bob), [])

    def test_groups_for_user_not_eligible(self):
        bob = create_member("bob", state_perm=False)
        bind(bob, "87654321")
        self.assertEqual(el.groups_for_user(bob), [])
