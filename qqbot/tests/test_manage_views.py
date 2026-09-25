"""Manager pages: groups, bindings, pending, settings, audit (SPEC section 6)."""

from datetime import timedelta
from unittest import mock

from django.contrib.auth.models import Group
from django.core.cache import cache
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from ..core import bindings as core_bindings
from ..models import AuditLog, Binding, BindCode, Config, Event, QQGroup
from ..views import manage
from .test_manage_security import create_manager
from .utils import add_to_groups, bind, create_group, create_member, put_in_roster

GROUPS = reverse("qqbot:manage_groups")
GROUP_CREATE = reverse("qqbot:manage_group_create")
BINDINGS = reverse("qqbot:manage_bindings")
PENDING = reverse("qqbot:manage_pending")
SETTINGS = reverse("qqbot:manage_settings")
AUDIT = reverse("qqbot:manage_audit")


def group_data(**kw):
    data = {
        "name": "聊天群",
        "group_id": "123456",
        "kind": "fixed",
        "description": "全联盟日常聊天",
        "sort_order": 10,
        "is_active": "on",
    }
    data.update(kw)
    return {k: v for k, v in data.items() if v is not None}


class ManagerTestCase(TestCase):
    def setUp(self):
        cache.clear()
        self.manager = create_manager()
        self.client.force_login(self.manager)

    def event_kinds(self):
        return list(Event.objects.order_by("id").values_list("kind", flat=True))


# --------------------------------------------------------------------------
# groups
# --------------------------------------------------------------------------


class GroupCrudTests(ManagerTestCase):
    def test_list_shows_groups(self):
        create_group("123456", name="联盟聊天群", description="日常")
        create_group("223456", kind="role", required=["Cap"], name="旗舰群")
        create_group("323456", kind="role", name="坏掉的小群")
        r = self.client.get(GROUPS)
        self.assertContains(r, "联盟聊天群")
        self.assertContains(r, "旗舰群")
        self.assertContains(r, "Cap")
        self.assertContains(r, "未选择（请编辑）")

    def test_create_fixed(self):
        r = self.client.post(GROUP_CREATE, group_data(group_id=" １２３ 456 "))
        self.assertRedirects(r, GROUPS)
        g = QQGroup.objects.get()
        self.assertEqual(g.group_id, "123456")
        self.assertEqual(g.kind, "fixed")
        self.assertTrue(g.is_active)
        self.assertEqual(self.event_kinds(), [Event.Kind.GROUPS, Event.Kind.RECHECK_ALL])
        log = AuditLog.objects.get()
        self.assertEqual(log.action, AuditLog.Action.GROUP)
        self.assertEqual(log.actor, self.manager)
        self.assertEqual(log.detail["op"], "create")
        self.assertEqual(log.detail["group_id"], "123456")

    def test_create_role_with_groups(self):
        cap = Group.objects.create(name="Cap")
        fc = Group.objects.create(name="FC")
        r = self.client.post(
            GROUP_CREATE, group_data(kind="role", required_groups=[cap.pk, fc.pk])
        )
        self.assertRedirects(r, GROUPS)
        g = QQGroup.objects.get()
        self.assertEqual(set(g.required_groups.all()), {cap, fc})
        self.assertEqual(AuditLog.objects.get().detail["required_groups"], ["Cap", "FC"])

    def test_role_without_groups_rejected(self):
        r = self.client.post(GROUP_CREATE, group_data(kind="role"))
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "身份组小群至少要选择一个 AA 组")
        self.assertFalse(QQGroup.objects.exists())
        self.assertFalse(Event.objects.exists())
        self.assertFalse(AuditLog.objects.exists())

    def test_fixed_with_groups_rejected(self):
        cap = Group.objects.create(name="Cap")
        r = self.client.post(GROUP_CREATE, group_data(required_groups=[cap.pk]))
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "固定群对所有成员开放")
        self.assertFalse(QQGroup.objects.exists())

    def test_invalid_group_id_rejected(self):
        for bad in ("", "0123456", "1234", "123456789012", "abcdef", "12345a"):
            with self.subTest(bad=bad):
                r = self.client.post(GROUP_CREATE, group_data(group_id=bad))
                self.assertEqual(r.status_code, 200)
                self.assertContains(r, "没有保存")
        self.assertFalse(QQGroup.objects.exists())

    def test_duplicate_group_id_rejected(self):
        create_group("123456")
        r = self.client.post(GROUP_CREATE, group_data(group_id="１２３４５６"))
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "这个群号已经添加过了")
        self.assertEqual(QQGroup.objects.count(), 1)
        self.assertFalse(Event.objects.exists())

    def test_edit(self):
        g = create_group("123456", name="旧名字")
        url = reverse("qqbot:manage_group_edit", args=[g.pk])
        r = self.client.get(url)
        self.assertContains(r, "旧名字")
        r = self.client.post(url, group_data(name="新名字", group_id="123456", is_active=None))
        self.assertRedirects(r, GROUPS)
        g.refresh_from_db()
        self.assertEqual(g.name, "新名字")
        self.assertFalse(g.is_active)
        self.assertEqual(self.event_kinds(), [Event.Kind.GROUPS, Event.Kind.RECHECK_ALL])
        log = AuditLog.objects.get()
        self.assertEqual(log.detail["op"], "update")
        self.assertIn("name", log.detail["changed"])
        self.assertIn("is_active", log.detail["changed"])

    def test_edit_keeps_own_group_id_but_rejects_another(self):
        create_group("223456")
        g = create_group("123456")
        url = reverse("qqbot:manage_group_edit", args=[g.pk])
        r = self.client.post(url, group_data(group_id="223456"))
        self.assertContains(r, "这个群号已经添加过了")
        g.refresh_from_db()
        self.assertEqual(g.group_id, "123456")

    def test_edit_to_fixed_must_clear_groups(self):
        g = create_group("123456", kind="role", required=["Cap"])
        url = reverse("qqbot:manage_group_edit", args=[g.pk])
        cap = Group.objects.get(name="Cap")
        r = self.client.post(url, group_data(kind="fixed", required_groups=[cap.pk]))
        self.assertContains(r, "固定群对所有成员开放")
        r = self.client.post(url, group_data(kind="fixed"))
        self.assertRedirects(r, GROUPS)
        g.refresh_from_db()
        self.assertEqual(g.kind, "fixed")
        self.assertFalse(g.required_groups.exists())

    def test_edit_without_changes_writes_nothing(self):
        g = create_group("123456", name="聊天群", description="全联盟日常聊天", sort_order=10)
        url = reverse("qqbot:manage_group_edit", args=[g.pk])
        r = self.client.post(url, group_data())
        self.assertRedirects(r, GROUPS)
        self.assertFalse(Event.objects.exists())
        self.assertFalse(AuditLog.objects.exists())

    def test_edit_unknown_group_404(self):
        r = self.client.get(reverse("qqbot:manage_group_edit", args=[999]))
        self.assertEqual(r.status_code, 404)

    def test_delete_confirm_then_post(self):
        g = create_group("123456", name="要删的群")
        put_in_roster(g, ["11111111"])
        url = reverse("qqbot:manage_group_delete", args=[g.pk])
        r = self.client.get(url)
        self.assertContains(r, "确认删除")
        self.assertContains(r, "要删的群")
        self.assertTrue(QQGroup.objects.filter(pk=g.pk).exists())
        r = self.client.post(url)
        self.assertRedirects(r, GROUPS)
        self.assertFalse(QQGroup.objects.filter(pk=g.pk).exists())
        self.assertEqual(self.event_kinds(), [Event.Kind.GROUPS, Event.Kind.RECHECK_ALL])
        log = AuditLog.objects.get()
        self.assertEqual(log.detail["op"], "delete")
        self.assertEqual(log.detail["group_id"], "123456")
        self.assertEqual(log.detail["name"], "要删的群")


# --------------------------------------------------------------------------
# bindings list
# --------------------------------------------------------------------------


class BindingListTests(ManagerTestCase):
    def setUp(self):
        super().setUp()
        self.alice = create_member("alice", character_name="Kaela Voss", corp_ticker="IGC")
        self.bob = create_member("bob", character_name="Bob Builder", corp_ticker="XYZ")
        self.carol = create_member("carol", character_name="Carol", corp_ticker="XYZ")
        self.dave = create_member("dave", character_name="Dave", corp_ticker="QWE")
        bind(self.alice, "11111111", status="verified", nickname="凯拉")
        bind(self.bob, "22222222", status="trusted")
        bind(self.carol, "33333333", status="trusted")
        bind(self.dave, "33333333", status="trusted")

    def usernames(self, response):
        return [item["binding"].user.username for item in response.context["items"]]

    def test_list_all_with_full_qq(self):
        r = self.client.get(BINDINGS)
        self.assertEqual(self.usernames(r), ["alice", "bob", "carol", "dave"])
        self.assertContains(r, "11111111")
        self.assertContains(r, "[IGC] Kaela Voss - 凯拉")

    def test_header_counts(self):
        group = create_group("123456")
        put_in_roster(group, ["44444444", "55555555", "11111111"])
        r = self.client.get(BINDINGS)
        summary = r.context["summary"]
        self.assertEqual(summary["bound"], 4)
        self.assertEqual(summary["verified"], 1)
        self.assertEqual(summary["trusted"], 3)
        self.assertEqual(summary["conflicts"], 1)
        self.assertEqual(summary["unbound"], 2)
        self.assertContains(r, 'id="qqbot-summary"')

    def test_search(self):
        cases = {
            "alic": ["alice"],  # username
            "kaela": ["alice"],  # character name
            "2222": ["bob"],  # QQ
            "xyz": ["bob", "carol"],  # corp ticker
            "凯拉": ["alice"],  # nickname
            "nobody": [],
        }
        for q, expected in cases.items():
            with self.subTest(q=q):
                r = self.client.get(BINDINGS, {"q": q})
                self.assertEqual(self.usernames(r), expected)

    def test_filter_status(self):
        r = self.client.get(BINDINGS, {"status": "verified"})
        self.assertEqual(self.usernames(r), ["alice"])
        r = self.client.get(BINDINGS, {"status": "trusted"})
        self.assertEqual(self.usernames(r), ["bob", "carol", "dave"])

    def test_filter_conflict(self):
        r = self.client.get(BINDINGS, {"conflict": "1"})
        self.assertEqual(self.usernames(r), ["carol", "dave"])
        self.assertTrue(all(item["conflict"] for item in r.context["items"]))

    def test_filter_combined(self):
        r = self.client.get(BINDINGS, {"conflict": "1", "q": "carol"})
        self.assertEqual(self.usernames(r), ["carol"])

    def test_invalid_status_ignored(self):
        r = self.client.get(BINDINGS, {"status": "bogus"})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(len(r.context["items"]), 4)
        r = self.client.get(BINDINGS, {"status": "bogus", "q": "bob"})
        self.assertEqual(self.usernames(r), ["bob"])

    def test_pagination(self):
        for i in range(60):
            bind(create_member(f"user{i:03d}"), str(50_000_000 + i), status="verified")
        r = self.client.get(BINDINGS)
        self.assertEqual(len(r.context["items"]), 50)
        self.assertEqual(r.context["page"].paginator.count, 64)
        self.assertContains(r, "第 1 / 2 页")
        r = self.client.get(BINDINGS, {"page": 2})
        self.assertEqual(len(r.context["items"]), 14)
        # Filters are kept in the page links.
        r = self.client.get(BINDINGS, {"q": "user", "page": 1})
        self.assertEqual(r.context["page"].paginator.count, 60)
        self.assertContains(r, "?q=user&amp;page=2")
        r = self.client.get(BINDINGS, {"page": "junk"})
        self.assertEqual(r.status_code, 200)

    def test_query_count_does_not_grow_with_rows(self):
        create_group("123456")
        self.client.get(BINDINGS)  # warm up (Config row, sessions)
        from django.db import connection
        from django.test.utils import CaptureQueriesContext

        with CaptureQueriesContext(connection) as small:
            self.client.get(BINDINGS)
        for i in range(20):
            bind(create_member(f"more{i}"), str(60_000_000 + i), status="verified")
        with CaptureQueriesContext(connection) as big:
            self.client.get(BINDINGS)
        self.assertLessEqual(len(big.captured_queries), len(small.captured_queries) + 2)


# --------------------------------------------------------------------------
# binding detail and actions
# --------------------------------------------------------------------------


class BindingDetailTests(ManagerTestCase):
    def setUp(self):
        super().setUp()
        self.fixed = create_group("123456", name="聊天群")
        self.cap_group = Group.objects.create(name="Cap")
        self.role = create_group("223456", kind="role", required=[self.cap_group], name="旗舰群")
        self.broken = create_group("323456", kind="role", name="配错的群")
        self.alice = create_member("alice", character_name="Kaela Voss")
        self.binding = bind(self.alice, "11111111", status="verified", nickname="凯拉")
        self.url = reverse("qqbot:manage_binding", args=[self.binding.pk])

    def table(self, response):
        return {row["group"].name: row for row in response.context["table"]}

    def test_decision_table(self):
        r = self.client.get(self.url)
        self.assertEqual(r.status_code, 200)
        table = self.table(r)
        self.assertEqual(table["聊天群"]["decision"], "allow")
        self.assertEqual(table["聊天群"]["reason_label"], "合格")
        self.assertEqual(table["旗舰群"]["reason_label"], "不在要求的组")
        self.assertEqual(table["配错的群"]["reason_label"], "群配置有误")
        self.assertContains(r, "不在要求的组")
        self.assertContains(r, "群配置有误")
        self.assertContains(r, "[IGC] Kaela Voss - 凯拉")
        self.assertContains(r, "11111111")

        add_to_groups(self.alice, self.cap_group)
        table = self.table(self.client.get(self.url))
        self.assertEqual(table["旗舰群"]["reason_label"], "合格")

    def test_decision_table_inactive_and_no_access(self):
        self.alice.is_active = False
        self.alice.save(update_fields=["is_active"])
        table = self.table(self.client.get(self.url))
        self.assertEqual(table["聊天群"]["reason_label"], "账号已停用")

        noperm = create_member("noperm", state_perm=False)
        b = bind(noperm, "22222222")
        r = self.client.get(reverse("qqbot:manage_binding", args=[b.pk]))
        self.assertEqual(self.table(r)["聊天群"]["reason_label"], "没有成员权限")

    def test_all_reasons_have_labels(self):
        from ..core import eligibility

        reasons = [
            eligibility.OK, eligibility.NOT_BOUND, eligibility.PENDING_VERIFY,
            eligibility.CONFLICT, eligibility.USER_INACTIVE, eligibility.NO_MAIN,
            eligibility.NO_ACCESS, eligibility.GROUP_ROLE_MISSING,
            eligibility.GROUP_MISCONFIGURED,
        ]
        for reason in reasons:
            self.assertIn(reason, manage.REASON_LABELS)
        self.assertEqual(manage.REASON_LABELS[eligibility.CONFLICT], "冲突")

    def test_conflict_detail(self):
        bob = create_member("bob")
        carol = create_member("carol")
        b1 = bind(bob, "33333333", status="trusted")
        bind(carol, "33333333", status="trusted")
        r = self.client.get(reverse("qqbot:manage_binding", args=[b1.pk]))
        self.assertTrue(r.context["conflict"])
        self.assertEqual(self.table(r)["聊天群"]["reason_label"], "冲突")
        self.assertContains(r, "carol")

    def test_unknown_binding_404(self):
        self.assertEqual(
            self.client.get(reverse("qqbot:manage_binding", args=[999])).status_code, 404
        )

    def test_set_and_clear_card_override(self):
        url = reverse("qqbot:manage_binding_card", args=[self.binding.pk])
        r = self.client.post(url, {"card": "  [IGC]  指挥官  "})
        self.assertRedirects(r, self.url)
        self.binding.refresh_from_db()
        self.assertEqual(self.binding.card_override, "[IGC] 指挥官")
        log = AuditLog.objects.get(action=AuditLog.Action.CARD)
        self.assertEqual(log.actor, self.manager)
        self.assertIn(Event.Kind.CARD, self.event_kinds())
        r = self.client.get(self.url)
        self.assertEqual(r.context["card"], "[IGC] 指挥官")
        self.assertContains(r, "恢复自动群名片")

        r = self.client.post(url, {"card": "ignored", "action": "clear"})
        self.assertRedirects(r, self.url)
        self.binding.refresh_from_db()
        self.assertEqual(self.binding.card_override, "")
        self.assertEqual(AuditLog.objects.filter(action=AuditLog.Action.CARD).count(), 2)

    def test_card_override_too_long_rejected(self):
        url = reverse("qqbot:manage_binding_card", args=[self.binding.pk])
        r = self.client.post(url, {"card": "长" * 21})  # 63 bytes
        self.assertEqual(r.status_code, 400)
        self.assertContains(r, "群名片太长", status_code=400)
        self.binding.refresh_from_db()
        self.assertEqual(self.binding.card_override, "")
        self.assertFalse(AuditLog.objects.exists())

    def test_confirm_resolves_conflict(self):
        bob = create_member("bob")
        carol = create_member("carol")
        b1 = bind(bob, "33333333", status="trusted")
        b2 = bind(carol, "33333333", status="trusted")
        r = self.client.post(reverse("qqbot:manage_binding_confirm", args=[b1.pk]),
                             {"qq": "33333333"})
        self.assertRedirects(r, reverse("qqbot:manage_binding", args=[b1.pk]))
        b1.refresh_from_db()
        self.assertEqual(b1.status, "verified")
        self.assertEqual(b1.verified_via, "manager")
        self.assertFalse(Binding.objects.filter(pk=b2.pk).exists())
        self.assertEqual(core_bindings.conflicts(), [])
        self.assertTrue(
            AuditLog.objects.filter(action=AuditLog.Action.CONFIRM, actor=self.manager).exists()
        )
        self.assertTrue(
            AuditLog.objects.filter(action=AuditLog.Action.CONFLICT_RESOLVED).exists()
        )

    def test_confirm_back_to_pending(self):
        bob = create_member("bob")
        b1 = bind(bob, "33333333", status="trusted")
        r = self.client.post(
            reverse("qqbot:manage_binding_confirm", args=[b1.pk]),
            {"back": "pending", "qq": "33333333"},
        )
        self.assertRedirects(r, PENDING)

    def test_confirm_ignores_unknown_back_target(self):
        bob = create_member("bob")
        b1 = bind(bob, "33333333", status="trusted")
        r = self.client.post(
            reverse("qqbot:manage_binding_confirm", args=[b1.pk]),
            {"back": "https://evil.example/", "qq": "33333333"},
        )
        self.assertRedirects(r, reverse("qqbot:manage_binding", args=[b1.pk]))

    def test_confirm_refused_when_someone_else_verified(self):
        bob = create_member("bob")
        b = bind(bob, "11111111", status="trusted")
        r = self.client.post(reverse("qqbot:manage_binding_confirm", args=[b.pk]),
                             {"qq": "11111111"}, follow=True)
        self.assertContains(r, "该 QQ 已被其他账号验证")
        b.refresh_from_db()
        self.assertEqual(b.status, "trusted")

    def test_force_unbind_needs_confirmation(self):
        url = reverse("qqbot:manage_binding_unbind", args=[self.binding.pk])
        r = self.client.get(self.url)
        self.assertContains(r, url)
        r = self.client.get(url)
        self.assertContains(r, "确认强制解绑")
        self.assertContains(r, "11111111")
        self.assertTrue(Binding.objects.filter(pk=self.binding.pk).exists())

        self.assertContains(r, 'name="qq" value="11111111"')
        r = self.client.post(url, {"qq": "11111111"})
        self.assertRedirects(r, BINDINGS)
        self.assertFalse(Binding.objects.filter(pk=self.binding.pk).exists())
        log = AuditLog.objects.get(action=AuditLog.Action.FORCE_UNBIND)
        self.assertEqual(log.actor, self.manager)
        self.assertEqual(log.target_user, self.alice)
        self.assertEqual(log.qq, "11111111")
        self.assertTrue(Event.objects.filter(kind=Event.Kind.RECHECK, qq="11111111").exists())

    def test_force_unbind_invalidates_code_and_can_go_back_to_pending(self):
        BindCode.objects.create(
            user=self.alice, qq="99999999", nickname="n", code_hash="x" * 64,
            expires_at=timezone.now() + timedelta(minutes=10),
        )
        url = reverse("qqbot:manage_binding_unbind", args=[self.binding.pk])
        r = self.client.get(url, {"back": "pending"})
        self.assertContains(r, 'name="back" value="pending"')
        r = self.client.post(url, {"back": "pending", "qq": "11111111"})
        self.assertRedirects(r, PENDING)
        self.assertIsNotNone(BindCode.objects.get().invalidated_at)

    def test_confirm_refused_when_member_rebound_meanwhile(self):
        """The page showed QQ X; the member rebound the same row to QQ Y."""
        bob = create_member("bob")
        b1 = bind(bob, "33333333", status="trusted")
        bind(create_member("carol"), "33333333", status="trusted")
        owner = create_member("dave")
        b_owner = bind(owner, "44444444", status="trusted")
        page = self.client.get(PENDING)
        self.assertContains(page, 'name="qq" value="33333333"')
        # bob moves his (same pk) binding to dave's QQ
        Binding.objects.filter(pk=b1.pk).update(qq="44444444")
        r = self.client.post(
            reverse("qqbot:manage_binding_confirm", args=[b1.pk]),
            {"back": "pending", "qq": "33333333"}, follow=True,
        )
        self.assertContains(r, "页面上的信息已经过时")
        b1.refresh_from_db()
        self.assertEqual(b1.status, "trusted")
        self.assertTrue(Binding.objects.filter(pk=b_owner.pk).exists())
        self.assertFalse(AuditLog.objects.filter(action=AuditLog.Action.CONFIRM).exists())

    def test_confirm_without_qq_is_refused(self):
        b1 = bind(create_member("bob"), "33333333", status="trusted")
        self.client.post(reverse("qqbot:manage_binding_confirm", args=[b1.pk]))
        b1.refresh_from_db()
        self.assertEqual(b1.status, "trusted")

    def test_force_unbind_refused_when_member_rebound_meanwhile(self):
        url = reverse("qqbot:manage_binding_unbind", args=[self.binding.pk])
        Binding.objects.filter(pk=self.binding.pk).update(qq="55555555", verified_qq="55555555")
        r = self.client.post(url, {"qq": "11111111"})
        self.assertRedirects(r, reverse("qqbot:manage_binding", args=[self.binding.pk]))
        self.assertTrue(Binding.objects.filter(pk=self.binding.pk).exists())
        self.assertFalse(AuditLog.objects.filter(action=AuditLog.Action.FORCE_UNBIND).exists())


# --------------------------------------------------------------------------
# pending
# --------------------------------------------------------------------------


class PendingTests(ManagerTestCase):
    def test_empty(self):
        r = self.client.get(PENDING)
        self.assertContains(r, "现在没有冲突")
        self.assertContains(r, "没有发现未绑定的 QQ")

    def test_lists_conflicts_with_actions(self):
        bob = create_member("bob", character_name="Bob Builder")
        carol = create_member("carol", character_name="Carol Char")
        b1 = bind(bob, "33333333", status="trusted")
        b2 = bind(carol, "33333333", status="trusted")
        bind(create_member("dave"), "44444444", status="trusted")  # no conflict
        r = self.client.get(PENDING)
        self.assertEqual([c["qq"] for c in r.context["conflicts"]], ["33333333"])
        self.assertContains(r, "Bob Builder")
        self.assertContains(r, "Carol Char")
        for b in (b1, b2):
            self.assertContains(r, reverse("qqbot:manage_binding_confirm", args=[b.pk]))
            self.assertContains(
                r, reverse("qqbot:manage_binding_unbind", args=[b.pk]) + "?back=pending"
            )
        self.assertNotContains(r, "44444444")
        self.assertEqual(r.context["summary"]["pending"], 1)
        # Each confirm form carries the QQ the manager is looking at.
        self.assertContains(r, 'name="qq" value="33333333"', count=2)
        # A QQ in a fresh roster cannot get a code, so the page must not send
        # managers down that path (it would be a dead end).
        self.assertNotContains(r, "用验证码完成验证")

    def test_lists_unbound_roster_by_group(self):
        g1 = create_group("123456", name="聊天群")
        g2 = create_group("223456", name="Ping 群")
        stale = create_group("323456", name="旧名单群")
        inactive = create_group("423456", name="停用群", is_active=False)
        bind(create_member("alice"), "11111111")
        put_in_roster(g1, ["11111111", "22222222", "33333333"])
        put_in_roster(g2, ["22222222", "44444444"])
        put_in_roster(stale, ["55555555"], now=timezone.now() - timedelta(days=30))
        put_in_roster(inactive, ["66666666"])
        r = self.client.get(PENDING)
        slots = {s["group"].name: s["qqs"] for s in r.context["unbound_groups"]}
        self.assertEqual(slots, {"聊天群": ["22222222", "33333333"], "Ping 群": ["22222222", "44444444"]})
        self.assertNotContains(r, "11111111")
        self.assertNotContains(r, "55555555")
        self.assertNotContains(r, "66666666")
        self.assertIn(stale, r.context["stale_groups"])
        self.assertContains(r, "旧名单群")
        # Distinct QQs in the header count.
        self.assertEqual(r.context["summary"]["unbound"], 3)


# --------------------------------------------------------------------------
# settings
# --------------------------------------------------------------------------


def settings_data(**kw):
    data = {
        "rules_text": "请遵守群规",
        "card_format": Config.DEFAULT_CARD_FORMAT,
        "code_ttl_minutes": 10,
        "roster_max_age_days": 7,
        "rebind_cooldown_hours": 24,
    }
    data.update(kw)
    return data


class SettingsTests(ManagerTestCase):
    def test_page_shows_example_card(self):
        r = self.client.get(SETTINGS)
        self.assertContains(r, "{corp_ticker}")
        self.assertContains(r, "[IGC] boss - 昵称")

    def test_save_without_card_change(self):
        with mock.patch("qqbot.tasks.queue_reconcile") as sched:
            with self.captureOnCommitCallbacks(execute=True):
                r = self.client.post(SETTINGS, settings_data(code_ttl_minutes=15))
        self.assertRedirects(r, SETTINGS)
        config = Config.get_solo()
        self.assertEqual(config.code_ttl_minutes, 15)
        self.assertEqual(config.rules_text, "请遵守群规")
        log = AuditLog.objects.get()
        self.assertEqual(log.action, AuditLog.Action.CONFIG)
        self.assertEqual(log.actor, self.manager)
        self.assertEqual(log.detail["code_ttl_minutes"], {"old": 10, "new": 15})
        self.assertIn("rules_text", log.detail["changed"])
        sched.assert_not_called()

    def test_card_format_change_triggers_reconcile(self):
        with mock.patch("qqbot.tasks.queue_reconcile") as sched:
            with self.captureOnCommitCallbacks(execute=True) as callbacks:
                r = self.client.post(SETTINGS, settings_data(card_format="{nickname}"))
        self.assertRedirects(r, SETTINGS)
        self.assertEqual(Config.get_solo().card_format, "{nickname}")
        self.assertEqual(len(callbacks), 1)
        sched.assert_called_once_with()
        log = AuditLog.objects.get()
        self.assertEqual(
            log.detail["card_format"], {"old": Config.DEFAULT_CARD_FORMAT, "new": "{nickname}"}
        )

    def test_card_format_change_refreshes_cards_end_to_end(self):
        create_group("123456")
        alice = create_member("alice", character_name="Kaela Voss")
        b = bind(alice, "11111111", nickname="凯拉")
        from ..core import events

        events.refresh_binding(b)
        Event.objects.all().delete()
        # Celery runs eagerly in the test settings: the real task runs.
        with self.captureOnCommitCallbacks(execute=True):
            self.client.post(SETTINGS, settings_data(card_format="{nickname}"))
        self.assertEqual(self.event_kinds(), [Event.Kind.CARD])

    def test_invalid_card_format_rejected(self):
        for bad in ("{unknown}", "{nickname", "{nickname!r}", "{nickname:>999}", "{0}", ""):
            with self.subTest(bad=bad):
                r = self.client.post(SETTINGS, settings_data(card_format=bad))
                self.assertEqual(r.status_code, 200)
                self.assertContains(r, "没有保存")
        self.assertEqual(Config.get_solo().card_format, Config.DEFAULT_CARD_FORMAT)
        self.assertFalse(AuditLog.objects.exists())

    def test_out_of_range_numbers_rejected(self):
        r = self.client.post(SETTINGS, settings_data(code_ttl_minutes=1))
        self.assertContains(r, "没有保存")
        self.assertEqual(Config.get_solo().code_ttl_minutes, 10)

    def test_browser_line_endings_are_not_a_change(self):
        config = Config.get_solo()
        data = settings_data(rules_text=config.rules_text.replace("\n", "\r\n"))
        r = self.client.post(SETTINGS, data)
        self.assertRedirects(r, SETTINGS)
        self.assertFalse(AuditLog.objects.exists())
        r = self.client.post(SETTINGS, settings_data(rules_text="第一行\r\n第二行"))
        self.assertEqual(Config.get_solo().rules_text, "第一行\n第二行")

    def test_no_change_writes_nothing(self):
        config = Config.get_solo()
        data = settings_data(rules_text=config.rules_text)
        r = self.client.post(SETTINGS, data)
        self.assertRedirects(r, SETTINGS)
        self.assertFalse(AuditLog.objects.exists())


# --------------------------------------------------------------------------
# audit
# --------------------------------------------------------------------------


class AuditTests(ManagerTestCase):
    def test_list_and_filter_by_qq(self):
        AuditLog.objects.create(action="bind", qq="11111111", actor_name="alice",
                                detail={"status": "trusted"})
        AuditLog.objects.create(action="unbind", qq="22222222", detail={})
        r = self.client.get(AUDIT)
        self.assertEqual(len(r.context["rows"]), 2)
        self.assertContains(r, "机器人 / 系统")
        self.assertContains(r, "绑定")
        r = self.client.get(AUDIT, {"qq": "１１１１１１１１"})
        self.assertEqual([row["entry"].qq for row in r.context["rows"]], ["11111111"])
        self.assertEqual(r.context["rows"][0]["details"], [("状态", "老成员免验证")])
        self.assertContains(r, "老成员免验证")

    def test_filter_by_action(self):
        AuditLog.objects.create(action="bind", qq="11111111")
        AuditLog.objects.create(action="unbind", qq="11111111")
        r = self.client.get(AUDIT, {"action": "unbind"})
        self.assertEqual([row["entry"].action for row in r.context["rows"]], ["unbind"])

    def test_invalid_qq_filter(self):
        AuditLog.objects.create(action="bind", qq="11111111")
        r = self.client.get(AUDIT, {"qq": "abc"})
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "QQ 号格式不正确")
        self.assertEqual(r.context["rows"], [])

    def test_pagination(self):
        AuditLog.objects.bulk_create(
            [AuditLog(action="bind", qq="11111111") for _ in range(55)]
            + [AuditLog(action="bind", qq="22222222") for _ in range(3)]
        )
        r = self.client.get(AUDIT)
        self.assertEqual(len(r.context["rows"]), 50)
        r = self.client.get(AUDIT, {"page": 2})
        self.assertEqual(len(r.context["rows"]), 8)
        r = self.client.get(AUDIT, {"qq": "11111111", "page": 2})
        self.assertEqual(len(r.context["rows"]), 5)
        self.assertContains(r, "qq=11111111&amp;page=1")

    def test_group_changes_appear(self):
        self.client.post(GROUP_CREATE, group_data())
        r = self.client.get(AUDIT)
        self.assertContains(r, "修改群配置")
        self.assertContains(r, "boss")
        details = dict(r.context["rows"][0]["details"])
        self.assertEqual(details["操作"], "新增")
        self.assertEqual(details["类型"], "固定群")
        self.assertEqual(details["群号"], "123456")
