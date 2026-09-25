"""The "QQ 绑定" card on AA's services page."""

import re
from pathlib import Path
from unittest import mock

from django.core.cache import cache
from django.db import connection
from django.db.models.signals import post_save
from django.test import RequestFactory, TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from allianceauth.eveonline.models import EveCharacter
from allianceauth.tests.auth_utils import AuthUtils

from .. import signals
from ..core import bindings, cards, events
from ..models import Binding, Event
from ..service_hook import QQBotService
from ..service_hook import logger as service_logger
from .test_signals import discard_pending
from .utils import MANAGE, bind, create_group, create_member, create_user, put_in_roster

SERVICES = reverse("services:services")
MY_QQ = reverse("qqbot:my_qq")
QQ = "12345678"
QQ_MASKED = "12****78"
CARD_MARK = 'fa-brands fa-qq fa-fw"></i> QQ 绑定'


class ServicesPageTests(TestCase):
    def setUp(self):
        cache.clear()

    def test_member_sees_card_unbound(self):
        user = create_member("member")
        self.client.force_login(user)
        r = self.client.get(SERVICES)
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, CARD_MARK)
        self.assertContains(r, "绑定后可以加入联盟 QQ 群")
        # Everything happens in the card: no link to a member page.
        self.assertNotContains(r, f'href="{MY_QQ}"')
        # Same outer classes as AA's cards, so it sits in AA's flex row.
        self.assertContains(r, '<div class="card mx-2 mb-3 ', count=1)

    def test_member_sees_masked_qq(self):
        user = create_member("member")
        bind(user, QQ, status=Binding.Status.VERIFIED)
        self.client.force_login(user)
        r = self.client.get(SERVICES)
        self.assertContains(r, CARD_MARK)
        self.assertContains(r, QQ_MASKED)
        self.assertContains(r, "已验证")
        self.assertNotContains(r, QQ)

    def test_pending_hint(self):
        user = create_member("member")
        self.assertEqual(bindings.submit(user, QQ, "凯拉").outcome, "pending")
        self.client.force_login(user)
        r = self.client.get(SERVICES)
        self.assertContains(r, "待验证")
        self.assertNotContains(r, QQ)

    def test_conflict_hint(self):
        user = create_member("member")
        other = create_member("other")
        bind(user, QQ, status=Binding.Status.TRUSTED)
        bind(other, QQ, status=Binding.Status.TRUSTED)
        self.client.force_login(user)
        r = self.client.get(SERVICES)
        self.assertContains(r, "冲突")
        self.assertContains(r, "请联系 QQ 管理员")

    def badge(self, user):
        self.client.force_login(user)
        html = self.client.get(SERVICES).content.decode()
        start = html.index(CARD_MARK)
        card = html[start:start + 2000]
        self.assertNotIn("Enabled", card)
        self.assertNotIn("Disabled", card)
        return card

    def test_badges(self):
        unbound = create_member("unbound")
        self.assertIn('text-bg-warning">未启用', self.badge(unbound))

        pending = create_member("pending")
        bindings.submit(pending, "22345678", "凯拉")
        self.assertIn('text-bg-primary">待验证', self.badge(pending))

        ok = create_member("ok")
        bind(ok, "32345678", status=Binding.Status.TRUSTED)
        self.assertIn('text-bg-success">已启用', self.badge(ok))

        a, b = create_member("a"), create_member("b")
        bind(a, QQ, status=Binding.Status.TRUSTED)
        bind(b, QQ, status=Binding.Status.TRUSTED)
        card = self.badge(a)
        self.assertIn('text-bg-danger">冲突', card)
        self.assertNotIn("text-bg-success", card)

        taken = create_member("taken")
        bind(taken, "42345678", status=Binding.Status.TRUSTED)
        bind(create_member("winner"), "42345678", status=Binding.Status.VERIFIED)
        card = self.badge(taken)
        self.assertIn('text-bg-danger">已被占用', card)
        self.assertNotIn("text-bg-success", card)

    def test_no_card_without_permission(self):
        user = create_member("noperm", state_perm=False)
        self.client.force_login(user)
        r = self.client.get(SERVICES)
        self.assertEqual(r.status_code, 200)
        self.assertNotContains(r, CARD_MARK)
        self.assertNotContains(r, 'id="qqbot"')


class HookTests(TestCase):
    def setUp(self):
        cache.clear()
        self.hook = QQBotService()

    def test_basics(self):
        self.assertEqual(self.hook.name, "qq")
        self.assertEqual(self.hook.title, "QQ 绑定")
        self.assertEqual(self.hook.access_perm, "qqbot.basic_access")
        self.assertTrue(self.hook.service_active_for_user(create_member("m")))
        self.assertFalse(self.hook.service_active_for_user(create_member("n", state_perm=False)))

    def test_callbacks_are_noops(self):
        user = create_member("m")
        binding = bind(user, QQ)
        self.hook.validate_user(user)
        self.assertFalse(self.hook.delete_user(user, notify_user=True))
        self.hook.update_groups(user)
        self.hook.update_all_groups()
        self.assertTrue(Binding.objects.filter(pk=binding.pk).exists())

    def test_sync_nickname_refreshes_after_commit(self):
        group = create_group("100001")
        user = create_member("m")
        put_in_roster(group, [QQ])
        bind(user, QQ)
        discard_pending()
        before = Event.objects.count()
        with self.captureOnCommitCallbacks(execute=True) as callbacks:
            self.hook.sync_nickname(user)
            # Nothing runs inside AA's (pre_save) transaction.
            self.assertEqual(Event.objects.count(), before)
        self.assertEqual(len(callbacks), 1)
        self.assertGreater(Event.objects.count(), before)

    def test_sync_nickname_sees_the_new_name(self):
        """AA calls sync_nickname from pre_save: the refresh must use the
        committed (new) data, not the old row."""
        create_group("100001")
        user = create_member("m", character_name="Old Name")
        bind(user, QQ, nickname="n")
        with self.captureOnCommitCallbacks(execute=True):
            events.refresh_user(user)  # fingerprint of the old card
        Event.objects.all().delete()
        char = user.profile.main_character
        char.character_name = "New Name"
        discard_pending()
        # Only what sync_nickname schedules: our own post_save receiver would
        # also catch the rename and hide the bug.
        uid = "qqbot_character_post_save"
        with self.captureOnCommitCallbacks(execute=True):
            self.hook.sync_nickname(user)  # as from pre_save: not saved yet
            post_save.disconnect(sender=EveCharacter, dispatch_uid=uid)
            try:
                char.save()
            finally:
                post_save.connect(signals.on_character_post_save, sender=EveCharacter,
                                  dispatch_uid=uid)
        self.assertEqual(
            list(Event.objects.filter(kind=Event.Kind.CARD).values_list("qq", flat=True)), [QQ]
        )
        self.assertIn("New Name", cards.render_card(Binding.objects.get(user=user)))

    def test_sync_nickname_swallows_errors(self):
        user = create_member("m")
        with mock.patch("qqbot.signals.schedule_refresh_user", side_effect=RuntimeError("boom")), \
                self.assertLogs(service_logger, "ERROR"):
            self.hook.sync_nickname(user)  # must not raise

    def test_no_useless_sync_groups_admin_action(self):
        """AA's User admin adds "Sync groups for ... accounts" for services that
        override update_groups; ours would do nothing."""
        from django.contrib.admin.sites import site

        from allianceauth.authentication.admin import UserAdmin

        admin = next(m for m in site._registry.values() if isinstance(m, UserAdmin))
        request = RequestFactory().get("/admin/")
        request.user = create_user("root", superuser=True)
        actions = admin.get_actions(request)
        self.assertFalse(any("qq" in name and "group" in name for name in actions), actions)
        self.assertIn("sync_qq_nickname", actions)

    def test_render_without_main(self):
        user = create_user("nomain", main=False)
        request = RequestFactory().get(SERVICES)
        request.user = user
        html = self.hook.render_services_ctrl(request)
        self.assertIn("你还没有设置主角色", html)
        self.assertIn('text-bg-warning">未启用', html)
        self.assertNotIn('name="qq"', html)
        self.assertNotIn(MY_QQ, html)

    def test_render_needs_few_queries(self):
        """The card renders on every services page load: a constant, small
        number of queries (no per-group queries)."""
        user = create_member("m")
        create_group("100001")
        create_group("100002")
        bind(user, QQ)
        request = RequestFactory().get(SERVICES)
        request.user = type(user).objects.get(pk=user.pk)
        request.session = {}
        self.hook.render_services_ctrl(request)  # fills AA's permission / profile caches
        with CaptureQueriesContext(connection) as queries:
            html = self.hook.render_services_ctrl(request)
        self.assertIn("100001", html)
        self.assertLessEqual(len(queries), 10, [q["sql"][:120] for q in queries])
        for i in range(3, 9):
            create_group(f"10000{i}")
        with CaptureQueriesContext(connection) as more:
            html = self.hook.render_services_ctrl(request)
        self.assertIn("100008", html)
        self.assertEqual(len(more), len(queries))


TEMPLATES = Path(__file__).resolve().parent.parent / "templates" / "qqbot"
CARD_TEMPLATES = [
    TEMPLATES / "service_ctrl.html",
    *sorted((TEMPLATES / "member").glob("*.html")),
]
# Every qqbot template: the card, base.html and the manager pages (managers
# use the same AA theme as everyone else, e.g. darkly).
ALL_TEMPLATES = sorted(TEMPLATES.rglob("*.html"))
# Light-only classes: wrong in AA's dark themes (darkly).
BANNED_CLASSES = {"bg-light", "bg-white", "table-light", "alert-light", "text-dark", "btn-light",
                  "text-black", "bg-dark", "border-light", "border-dark", "text-bg-light",
                  "btn-outline-light", "btn-outline-dark", "list-group-item-light",
                  # AA's darkly keeps Bootstrap's light root variables, so these
                  # come out light / dark-brown on its dark cards.
                  "bg-body-tertiary", "bg-body-secondary", "bg-body", "text-body-emphasis",
                  # darkly's "secondary" is #444: the colour of the card header
                  # and footer, and unreadable as text on the #303030 card.
                  "btn-secondary", "btn-outline-secondary", "text-bg-secondary", "bg-secondary",
                  "text-secondary", "border-secondary"}


class CardThemeTests(TestCase):
    """Theme rules for all qqbot templates (docs/SPEC.md 5)."""

    def classes(self, text):
        # Also the classes inside {% if %} branches of a class attribute.
        text = re.sub(r"\{%.*?%\}", " ", text)
        for attr in re.findall(r'class="([^"]*)"', text):
            yield from attr.split()

    def test_templates_exist(self):
        self.assertTrue(CARD_TEMPLATES[0].is_file())
        self.assertGreaterEqual(len(CARD_TEMPLATES), 3)
        names = {p.relative_to(TEMPLATES).as_posix() for p in ALL_TEMPLATES}
        self.assertLessEqual({"base.html", "manage/groups.html", "manage/binding_detail.html",
                              "manage/bindings.html", "manage/pending.html", "manage/audit.html"}, names)

    def test_class_scan_sees_conditional_classes(self):
        text = '<div class="{% if a %}text-body-secondary{% else %}text-warning-emphasis{% endif %}">'
        self.assertIn("text-warning-emphasis", set(self.classes(text)))

    def test_no_light_only_classes(self):
        for path in ALL_TEMPLATES:
            text = path.read_text(encoding="utf-8")
            with self.subTest(template=path.name):
                used = set(self.classes(text))
                self.assertEqual(used & BANNED_CLASSES, set())
                self.assertEqual({c for c in used if c.endswith("-emphasis")}, set())

    def test_no_fixed_colours(self):
        for path in ALL_TEMPLATES:
            text = path.read_text(encoding="utf-8")
            with self.subTest(template=path.name):
                self.assertNotRegex(text, r"#[0-9a-fA-F]{3,8}\b")
                self.assertNotRegex(text, r"\b(rgb|rgba|hsl)\(")
                for style in re.findall(r'style="([^"]*)"', text):
                    self.assertNotIn("color", style)
                    self.assertNotIn("background", style)

    def test_no_safe_filter(self):
        for path in ALL_TEMPLATES:
            text = path.read_text(encoding="utf-8")
            with self.subTest(template=path.name):
                self.assertNotIn("|safe", text)
                self.assertNotIn("autoescape off", text)


class CardIdTests(TestCase):
    """DOM ids in the card are unique and prefixed with "qqbot"."""

    def setUp(self):
        cache.clear()

    def assert_ids(self, user):
        self.client.force_login(user)
        html = self.client.get(SERVICES).content.decode()
        start = html.index('id="qqbot"')
        end = html.index('<h4 class="border-bottom">', start)  # AA's legend
        ids = re.findall(r'\bid="([^"]*)"', html[start:end])
        self.assertEqual(len(ids), len(set(ids)), ids)
        self.assertTrue(all(i == "qqbot" or i.startswith("qqbot-") for i in ids), ids)
        return ids

    def test_every_state(self):
        create_group("100001")
        unbound = create_member("unbound")
        self.assert_ids(unbound)

        pending = create_member("pending")
        self.client.force_login(pending)
        self.client.post(reverse("qqbot:member_submit"), {"qq": "22345678", "nickname": "凯拉"})
        self.assertIn("qqbot-code", self.assert_ids(pending))

        bound = create_member("bound")
        bind(bound, QQ)
        AuthUtils.add_permission_to_user_by_name(MANAGE, bound, disconnect_signals=True)
        ids = self.assert_ids(type(bound).objects.get(pk=bound.pk))
        for i in ("qqbot-nickname-panel", "qqbot-rebind-panel", "qqbot-unbind-panel",
                  "qqbot-unbind-confirm", "qqbot-manage-link"):
            self.assertIn(i, ids)
