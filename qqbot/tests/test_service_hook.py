"""The "QQ 绑定" card on AA's services page."""

from unittest import mock

from django.core.cache import cache
from django.test import RequestFactory, TestCase
from django.urls import reverse

from ..core import bindings
from ..models import Binding, Event
from ..service_hook import QQBotService
from ..service_hook import logger as service_logger
from .utils import bind, create_group, create_member, create_user, put_in_roster

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
        self.assertContains(r, "还没有绑定 QQ。")
        self.assertContains(r, f'href="{MY_QQ}"')

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

    def test_no_card_without_permission(self):
        user = create_member("noperm", state_perm=False)
        self.client.force_login(user)
        r = self.client.get(SERVICES)
        self.assertEqual(r.status_code, 200)
        self.assertNotContains(r, CARD_MARK)
        self.assertNotContains(r, "还没有绑定 QQ。")


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

    def test_sync_nickname_refreshes(self):
        group = create_group("100001")
        user = create_member("m")
        put_in_roster(group, [QQ])
        bind(user, QQ)
        before = Event.objects.count()
        self.hook.sync_nickname(user)
        self.assertGreater(Event.objects.count(), before)

    def test_sync_nickname_swallows_errors(self):
        user = create_member("m")
        with mock.patch("qqbot.core.events.refresh_user", side_effect=RuntimeError("boom")), \
                self.assertLogs(service_logger, "ERROR"):
            self.hook.sync_nickname(user)  # must not raise

    def test_render_without_main(self):
        user = create_user("nomain", main=False)
        request = RequestFactory().get(SERVICES)
        request.user = user
        html = self.hook.render_services_ctrl(request)
        self.assertIn("请先设置主角色", html)
        self.assertIn(MY_QQ, html)
